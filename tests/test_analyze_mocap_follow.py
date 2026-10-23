import math

import pytest

from scripts import analyze_mocap_follow as follow


def _yaw_quat_y_up(degrees):
    half = math.radians(degrees) / 2
    return (0.0, math.sin(half), 0.0, math.cos(half))


def _write_motive(path, frames, units="Meters"):
    """frames: [(time, {body: (x, y, z, qx, qy, qz, qw)})]"""
    bodies = list(frames[0][1])
    lines = [
        f"Format Version,1.23,Rotation Type,Quaternion,Length Units,{units}",
        "",
        ",Type," + ",".join(["Rigid Body"] * 7 * len(bodies)),
        ",Name," + ",".join(name for name in bodies for _ in range(7)),
        ",ID," + ",".join(["1"] * 7 * len(bodies)),
        ",," + ",".join((["Rotation"] * 4 + ["Position"] * 3) * len(bodies)),
        "Frame,Time (Seconds)," + ",".join(["X", "Y", "Z", "W", "X", "Y", "Z"] * len(bodies)),
    ]
    for index, (time, poses) in enumerate(frames):
        cells = [str(index), f"{time:.4f}"]
        for name in bodies:
            pose = poses[name]
            if pose is None:
                cells += [""] * 7
            else:
                x, y, z, qx, qy, qz, qw = pose
                cells += [str(v) for v in (qx, qy, qz, qw, x, y, z)]
        lines.append(",".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_rotate_unwraps_two_turns(tmp_path):
    frames = []
    for i in range(1001):
        t = i / 100
        human = 720 * i / 1000
        robot = 690 * i / 1000
        frames.append((t, {
            "Human": (0, 1, 0, *_yaw_quat_y_up(human)),
            "Robot": (1, 0.8, 0, *_yaw_quat_y_up(robot)),
        }))
    csv_path = tmp_path / "take.csv"
    _write_motive(csv_path, frames)

    human = follow.load_track(str(csv_path), "Human")
    robot = follow.load_track(str(csv_path), "Robot")
    h, r = follow.report_rotate(human, robot, "y")

    assert h["end"] == pytest.approx(720, abs=0.1)
    assert r["end"] == pytest.approx(690, abs=0.1)


def test_rotate_left_then_right_reports_peak(tmp_path):
    frames = []
    for i in range(401):
        angle = 180 * i / 200 if i <= 200 else 180 - 180 * (i - 200) / 200
        frames.append((i / 100, {
            "Human": (0, 1, 0, *_yaw_quat_y_up(angle)),
            "Robot": (0, 1, 0, *_yaw_quat_y_up(angle * 0.9)),
        }))
    csv_path = tmp_path / "take.csv"
    _write_motive(csv_path, frames)

    stats = follow.rotate_stats(follow.load_track(str(csv_path), "Robot"), "y")

    assert stats["max"] == pytest.approx(162, abs=0.1)
    assert stats["end"] == pytest.approx(0, abs=0.1)


def test_walk_measures_extent_and_deviation_in_millimeters(tmp_path):
    frames = []
    for i in range(201):
        s = 10000 * i / 200
        robot_offset = 50 * math.sin(math.pi * i / 200)
        frames.append((i / 20, {
            "Human": (s, 1000, 0, *_yaw_quat_y_up(0)),
            "Robot": (0.9 * s, 800, robot_offset, *_yaw_quat_y_up(0)),
        }))
    csv_path = tmp_path / "take.csv"
    _write_motive(csv_path, frames, units="Millimeters")

    human = follow.load_track(str(csv_path), "Human")
    robot = follow.load_track(str(csv_path), "Robot")
    h, r = follow.report_walk(human, robot, "y")

    assert h["extent"] == pytest.approx(10.0, abs=1e-6)
    assert r["extent"] == pytest.approx(9.0, abs=1e-3)
    assert r["deviation_range"] == pytest.approx(0.05, abs=2e-3)


def test_missing_frames_are_skipped_and_gap_is_warned(tmp_path):
    frames = []
    for i in range(101):
        pose = None if 40 <= i < 70 else (0, 1, 0, *_yaw_quat_y_up(i))
        frames.append((i / 100, {"Human": pose, "Robot": (0, 1, 0, *_yaw_quat_y_up(i))}))
    csv_path = tmp_path / "take.csv"
    _write_motive(csv_path, frames)

    human = follow.load_track(str(csv_path), "Human")
    follow.rotate_stats(human, "y")

    assert len(human.times) == 71
    assert any("丢帧" in warning for warning in human.warnings)


def test_unknown_body_lists_available_names(tmp_path):
    csv_path = tmp_path / "take.csv"
    _write_motive(csv_path, [(0, {"Human": (0, 0, 0, 0, 0, 0, 1)}),
                             (0.01, {"Human": (0, 0, 0, 0, 0, 0, 1)})])

    with pytest.raises(ValueError, match="Human"):
        follow.load_track(str(csv_path), "Robot")


def test_plain_csv_with_column_names(tmp_path):
    csv_path = tmp_path / "plain.csv"
    rows = ["time,x,y,z,qx,qy,qz,qw"]
    for i in range(11):
        qx, qy, qz, qw = _yaw_quat_y_up(36 * i)
        rows.append(f"{i * 0.1},{i},0,0,{qx},{qy},{qz},{qw}")
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    track = follow.load_track(str(csv_path), "x,y,z,qx,qy,qz,qw")

    assert follow.rotate_stats(track, "y")["end"] == pytest.approx(360, abs=0.1)
    assert follow.main(["walk", "--csv", str(csv_path), "--human", "x,y,z,qx,qy,qz,qw",
                        "--robot", "x,y,z,qx,qy,qz,qw"]) == 0
