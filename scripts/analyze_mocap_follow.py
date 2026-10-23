#!/usr/bin/env python3
"""对比动捕中人与机器人刚体的转身角度和直线行走，用于《Oli遥操跟随效果测试》。

支持两种 CSV：
  1. OptiTrack Motive 刚体导出（多行表头，四元数为 X,Y,Z,W）；--human/--robot 填刚体名。
  2. 普通单行表头 CSV；--human/--robot 填 7 个列名，顺序 x,y,z,qx,qy,qz,qw。

示例：
  python3 scripts/analyze_mocap_follow.py rotate --csv take.csv --human Human --robot Robot
  python3 scripts/analyze_mocap_follow.py walk --csv take.csv --human Human --robot Robot --up y
"""

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field

LENGTH_UNITS = {"meters": 1.0, "centimeters": 0.01, "millimeters": 0.001}
GAP_WARN_SECONDS = 0.2


@dataclass
class Track:
    name: str
    times: list = field(default_factory=list)
    positions: list = field(default_factory=list)  # (x, y, z)，单位米
    quats: list = field(default_factory=list)  # (qx, qy, qz, qw)
    warnings: list = field(default_factory=list)


def _to_float(text):
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.reader(handle))


def _find_motive_header(rows):
    for index, row in enumerate(rows):
        if row and row[0].strip() == "Frame" and index >= 2:
            return index
    return None


def _motive_scale(rows):
    meta = rows[0] if rows else []
    for key, value in zip(meta[::2], meta[1::2]):
        key = key.strip().lower()
        if key == "rotation type" and value.strip().lower() != "quaternion":
            raise ValueError(f"Motive 导出的旋转类型是 {value!r}，请重新导出为 Quaternion")
        if key == "length units":
            unit = value.strip().lower()
            if unit not in LENGTH_UNITS:
                raise ValueError(f"无法识别的长度单位 {value!r}")
            return LENGTH_UNITS[unit]
    return 1.0


def _motive_columns(rows, header_index, body_name):
    kind_row = rows[header_index - 1]
    axis_row = rows[header_index]
    type_row = name_row = None
    for row in rows[1:header_index]:
        label = row[1].strip() if len(row) > 1 else ""
        if label == "Type":
            type_row = row
        elif label == "Name":
            name_row = row
    if name_row is None:
        raise ValueError("Motive 表头中找不到 Name 行")

    wanted = {
        ("Position", "X"): 0, ("Position", "Y"): 1, ("Position", "Z"): 2,
        ("Rotation", "X"): 3, ("Rotation", "Y"): 4, ("Rotation", "Z"): 5, ("Rotation", "W"): 6,
    }
    columns = [None] * 7
    for col, name in enumerate(name_row):
        if name.strip() != body_name:
            continue
        if type_row is not None and col < len(type_row) and type_row[col].strip() != "Rigid Body":
            continue
        key = (kind_row[col].strip(), axis_row[col].strip())
        if key in wanted and columns[wanted[key]] is None:
            columns[wanted[key]] = col
    if None in columns:
        names = sorted({n.strip() for n in name_row[2:] if n.strip() and ":" not in n})
        raise ValueError(f"找不到刚体 {body_name!r} 的完整位置/四元数列；文件中的名称：{names}")
    return columns


def load_track(path, body, scale=None):
    """body 为 Motive 刚体名，或逗号分隔的 7 个列名（x,y,z,qx,qy,qz,qw）。"""
    rows = _read_rows(path)
    header_index = _find_motive_header(rows)
    if "," in body:
        names = [name.strip() for name in body.split(",")]
        if len(names) != 7:
            raise ValueError("列名需要 7 个：x,y,z,qx,qy,qz,qw")
        header_index = header_index if header_index is not None else 0
        header = [cell.strip() for cell in rows[header_index]]
        missing = [name for name in names if name not in header]
        if missing:
            raise ValueError(f"CSV 表头中没有这些列：{missing}")
        columns = [header.index(name) for name in names]
        time_col = next((i for i, h in enumerate(header) if h.lower().startswith("time")), None)
        unit_scale = 1.0
    else:
        if header_index is None:
            raise ValueError("不是 Motive 多行表头格式；普通 CSV 请用 7 个列名指定")
        columns = _motive_columns(rows, header_index, body)
        time_col = 1
        unit_scale = _motive_scale(rows)
    if scale is not None:
        unit_scale = scale

    track = Track(name=body)
    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if len(row) <= max(columns):
            continue
        values = [_to_float(row[col]) for col in columns]
        if any(value is None for value in values):
            continue
        time = _to_float(row[time_col]) if time_col is not None and time_col < len(row) else None
        if time is None:
            time = float(row_number)
        x, y, z, qx, qy, qz, qw = values
        norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
        if norm < 1e-6:
            continue
        track.times.append(time)
        track.positions.append((x * unit_scale, y * unit_scale, z * unit_scale))
        track.quats.append((qx / norm, qy / norm, qz / norm, qw / norm))
    if len(track.times) < 2:
        raise ValueError(f"{body!r} 的有效帧少于 2 帧")
    return track


def trim(track, start=None, end=None):
    keep = [i for i, t in enumerate(track.times)
            if (start is None or t >= start) and (end is None or t <= end)]
    if len(keep) < 2:
        raise ValueError(f"{track.name!r} 在指定时间窗内有效帧少于 2 帧")
    return Track(
        name=track.name,
        times=[track.times[i] for i in keep],
        positions=[track.positions[i] for i in keep],
        quats=[track.quats[i] for i in keep],
        warnings=list(track.warnings),
    )


def yaw_degrees(quat, up):
    """刚体局部 X 轴旋转后投影到水平面的方向角；从上往下看逆时针（左转）为正。"""
    qx, qy, qz, qw = quat
    r00 = 1 - 2 * (qy * qy + qz * qz)
    r10 = 2 * (qx * qy + qw * qz)
    r20 = 2 * (qx * qz - qw * qy)
    if up == "y":
        horizontal = (r00, -r20)
    else:
        horizontal = (r00, r10)
    if math.hypot(*horizontal) < 0.3:
        return None
    return math.degrees(math.atan2(horizontal[1], horizontal[0]))


def unwrapped_yaw(track, up):
    times, yaws = [], []
    skipped = 0
    for time, quat in zip(track.times, track.quats):
        yaw = yaw_degrees(quat, up)
        if yaw is None:
            skipped += 1
            continue
        if yaws:
            delta = (yaw - yaws[-1] + 180.0) % 360.0 - 180.0
            yaw = yaws[-1] + delta
        times.append(time)
        yaws.append(yaw)
    if skipped:
        track.warnings.append(f"{skipped} 帧刚体 X 轴接近竖直，已跳过；建刚体时应让人/机器人直立")
    if len(yaws) < 2:
        raise ValueError(f"{track.name!r} 可用于计算朝向的帧少于 2 帧")
    _warn_gaps(track, times)
    return times, [yaw - yaws[0] for yaw in yaws]


def _warn_gaps(track, times):
    gaps = [b - a for a, b in zip(times, times[1:])]
    worst = max(gaps)
    if worst > GAP_WARN_SECONDS:
        track.warnings.append(f"最大丢帧间隔 {worst:.3f} s，期间快速转身可能导致圈数算错")


def rotate_stats(track, up):
    _, yaws = unwrapped_yaw(track, up)
    return {"end": yaws[-1], "max": max(yaws), "min": min(yaws)}


def horizontal_points(track, up):
    if up == "y":
        return [(x, z) for x, _, z in track.positions]
    return [(x, y) for x, y, _ in track.positions]


def walk_stats(track, up):
    _warn_gaps(track, track.times)
    points = horizontal_points(track, up)
    count = len(points)
    cx = sum(p[0] for p in points) / count
    cy = sum(p[1] for p in points) / count
    sxx = sum((p[0] - cx) ** 2 for p in points)
    syy = sum((p[1] - cy) ** 2 for p in points)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in points)
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ux, uy = math.cos(angle), math.sin(angle)
    along = [(p[0] - cx) * ux + (p[1] - cy) * uy for p in points]
    across = [-(p[0] - cx) * uy + (p[1] - cy) * ux for p in points]
    start, end = points[0], points[-1]
    return {
        "extent": max(along) - min(along),
        "net": math.hypot(end[0] - start[0], end[1] - start[1]),
        "max_deviation": max(abs(value) for value in across),
        "deviation_range": max(across) - min(across),
    }


def _print_warnings(*tracks):
    for track in tracks:
        for warning in track.warnings:
            print(f"[警告] {track.name}: {warning}")


def report_rotate(human, robot, up):
    h = rotate_stats(human, up)
    r = rotate_stats(robot, up)
    print("转身对比（单位：度，左转为正，相对起始朝向）")
    print(f"{'':12}{'人':>10}{'机器人':>10}{'机器人-人':>12}")
    for key, label in (("end", "结束时"), ("max", "最大左转"), ("min", "最大右转")):
        print(f"{label:<10}{h[key]:>10.1f}{r[key]:>10.1f}{r[key] - h[key]:>12.1f}")
    _print_warnings(human, robot)
    return h, r


def report_walk(human, robot, up):
    h = walk_stats(human, up)
    r = walk_stats(robot, up)
    print("直线行走对比（距离单位：米，偏离单位：厘米；直线为各自轨迹的最佳拟合线）")
    print(f"{'':16}{'人':>10}{'机器人':>10}")
    print(f"{'行走范围(m)':<14}{h['extent']:>10.3f}{r['extent']:>10.3f}")
    print(f"{'起终点直线距离(m)':<10}{h['net']:>10.3f}{r['net']:>10.3f}")
    print(f"{'最大偏离(cm)':<14}{h['max_deviation'] * 100:>10.1f}{r['max_deviation'] * 100:>10.1f}")
    print(f"{'左右摆动范围(cm)':<11}{h['deviation_range'] * 100:>10.1f}{r['deviation_range'] * 100:>10.1f}")
    if h["extent"] > 0:
        print(f"机器人/人 行走范围比例：{r['extent'] / h['extent']:.3f}")
    _print_warnings(human, robot)
    return h, r


def main(argv=None):
    parser = argparse.ArgumentParser(description="Oli 遥操跟随效果：动捕转身/直线行走对比")
    parser.add_argument("mode", choices=("rotate", "walk"), help="rotate=转身角度，walk=直线行走")
    parser.add_argument("--csv", required=True, help="动捕 CSV（人的数据，若 --robot-csv 未给则两者同一文件）")
    parser.add_argument("--robot-csv", help="机器人数据单独导出时的 CSV")
    parser.add_argument("--human", required=True, help="人的刚体名，或 7 个列名 x,y,z,qx,qy,qz,qw")
    parser.add_argument("--robot", required=True, help="机器人的刚体名，或 7 个列名")
    parser.add_argument("--up", choices=("y", "z"), default="y", help="竖直轴，Motive 默认 y")
    parser.add_argument("--scale", type=float, help="位置乘以该系数换算成米（默认按 Motive 单位或 1.0）")
    parser.add_argument("--start", type=float, help="只分析该时间(秒)之后")
    parser.add_argument("--end", type=float, help="只分析该时间(秒)之前")
    args = parser.parse_args(argv)

    try:
        human = trim(load_track(args.csv, args.human, args.scale), args.start, args.end)
        robot = trim(load_track(args.robot_csv or args.csv, args.robot, args.scale), args.start, args.end)
        if args.mode == "rotate":
            report_rotate(human, robot, args.up)
        else:
            report_walk(human, robot, args.up)
    except (OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
