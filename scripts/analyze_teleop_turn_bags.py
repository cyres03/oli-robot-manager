#!/usr/bin/env python3
"""从全身遥操作录包中提取转身相关指标：指令朝向（/teleop/wb_cmd base_link）、
机器人实际朝向（/ImuData），以及会话状态、故障、姿态稳定性。

用法：python3 scripts/analyze_teleop_turn_bags.py 包1.bag [包2.bag ...] [--label B_left720 ...]
"""

import argparse
import math
import sys

import rosbag

TOPICS = ["/ImuData", "/teleop/wb_cmd", "/teleop/session_state", "/mrosout",
          "/diagnostics_value", "/wbt_tracking_err"]
ERROR_LEVEL = 8  # mros_msgs/Log 与 rosgraph 一致：8=ERROR，16=FATAL


def quat_yaw_z_up(x, y, z, w):
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def unwrap_deg(values):
    out = []
    for value in values:
        deg = math.degrees(value)
        if out:
            deg = out[-1] + (deg - out[-1] + 180.0) % 360.0 - 180.0
        out.append(deg)
    return out


def summarize_yaw(values):
    if len(values) < 2:
        return None
    rel = [v - values[0] for v in values]
    return {"end": rel[-1], "max": max(rel), "min": min(rel)}


def analyze(path):
    imu_yaw, imu_roll, imu_pitch, imu_gyro_z = [], [], [], []
    cmd_yaw = []
    states, faults, modes, not_following = {}, set(), set(), set()
    errors, diag = [], []
    track_err = []
    bag = rosbag.Bag(path)
    for topic, msg, _ in bag.read_messages(topics=TOPICS):
        if topic == "/ImuData":
            imu_roll.append(msg.euler[0])
            imu_pitch.append(msg.euler[1])
            imu_yaw.append(msg.euler[2])
            imu_gyro_z.append(msg.gyro[2])
        elif topic == "/teleop/wb_cmd":
            for anchor in msg.anchors:
                if anchor.name == "base_link":
                    o = anchor.pose.orientation
                    cmd_yaw.append(quat_yaw_z_up(o.x, o.y, o.z, o.w))
        elif topic == "/teleop/session_state":
            states[msg.session_state] = states.get(msg.session_state, 0) + 1
            modes.add(msg.mode)
            if msg.fault_code:
                faults.add((msg.fault_code, msg.fault_detail))
            for limb in msg.limbs:
                if limb.name == "head" and not limb.following:
                    not_following.add(limb.name)
        elif topic == "/mrosout" and msg.level >= ERROR_LEVEL:
            errors.append(f"{msg.name}: {msg.msg[:120]}")
        elif topic == "/diagnostics_value":
            diag.append(f"level={msg.level} {msg.name} code={msg.code} {msg.message[:80]}")
        elif topic == "/wbt_tracking_err":
            track_err.append(list(msg.data))
    duration = bag.get_end_time() - bag.get_start_time()
    bag.close()

    result = {
        "duration": duration,
        "imu": summarize_yaw(unwrap_deg(imu_yaw)),
        "cmd": summarize_yaw(unwrap_deg(cmd_yaw)),
        "max_roll": max((abs(math.degrees(v)) for v in imu_roll), default=float("nan")),
        "max_pitch": max((abs(math.degrees(v)) for v in imu_pitch), default=float("nan")),
        "max_yaw_rate": max((abs(math.degrees(v)) for v in imu_gyro_z), default=float("nan")),
        "states": states, "modes": modes, "faults": faults,
        "errors": errors, "diag": diag,
    }
    if track_err:
        dims = len(track_err[0])
        result["track_err_mean"] = [sum(r[i] for r in track_err) / len(track_err) for i in range(dims)]
        result["track_err_max"] = [max(r[i] for r in track_err) for i in range(dims)]
    return result


def fmt(stats, key):
    return f"{stats[key]:8.1f}" if stats else "     n/a"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bags", nargs="+")
    parser.add_argument("--label", nargs="*", help="与录包一一对应的名称")
    args = parser.parse_args(argv)
    labels = args.label or args.bags
    if len(labels) != len(args.bags):
        parser.error("--label 数量需与录包一致")

    print(f"{'名称':<16}{'时长s':>7}{'指令末':>9}{'实际末':>9}{'实际-指令':>10}"
          f"{'指令最大左':>10}{'实际最大左':>10}{'|roll|max':>10}{'|pitch|max':>11}{'yaw速率max':>11}")
    details = []
    for label, path in zip(labels, args.bags):
        r = analyze(path)
        gap = (r["imu"]["end"] - r["cmd"]["end"]) if r["imu"] and r["cmd"] else float("nan")
        print(f"{label:<16}{r['duration']:7.1f}{fmt(r['cmd'], 'end')}{fmt(r['imu'], 'end')}{gap:10.1f}"
              f"{fmt(r['cmd'], 'max')}{fmt(r['imu'], 'max')}{r['max_roll']:10.1f}{r['max_pitch']:11.1f}"
              f"{r['max_yaw_rate']:11.1f}")
        details.append((label, r))
    print("\n单位：度、度/秒；左转为正；朝向均相对包内第一帧")
    for label, r in details:
        print(f"\n[{label}] 会话状态计数={r['states']} 模式={sorted(r['modes'])} 故障={sorted(r['faults']) or '无'}")
        if "track_err_mean" in r:
            print("  wbt_tracking_err 均值=" + ",".join(f"{v:.3f}" for v in r["track_err_mean"]))
            print("  wbt_tracking_err 最大=" + ",".join(f"{v:.3f}" for v in r["track_err_max"]))
        print(f"  mrosout ERROR 及以上 {len(r['errors'])} 条" + "".join(f"\n    {e}" for e in r["errors"][:5]))
        print(f"  diagnostics_value {len(r['diag'])} 条" + "".join(f"\n    {d}" for d in r["diag"][:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
