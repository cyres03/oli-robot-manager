"""Offline endpoint settling analysis for optical two-rigid-body CSV recordings."""

import argparse
import csv
from dataclasses import dataclass
import hashlib
import html
import io
import json
import math
from pathlib import Path
import statistics
import unittest
import xml.etree.ElementTree as ET
import zipfile


@dataclass
class Recording:
    name: str
    metadata: dict
    frames: list
    times: list
    camera_times: list
    positions: dict


def read_recording(archive, member):
    with archive.open(member) as stream:
        reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
        headers = [next(reader) for _ in range(7)]
        metadata = dict(zip(headers[0][::2], headers[0][1::2]))
        if metadata.get("LengthUnits") != "mm":
            raise ValueError(f"{member}: expected millimetres")
        if headers[6][:3] != ["Frame", "Time", "CameraSyncTime"]:
            raise ValueError(f"{member}: unexpected time columns")
        columns = {}
        for name in ("Rigid1", "Rigid2"):
            columns[name] = [
                next(
                    index
                    for index in range(len(headers[3]))
                    if headers[2][index] == "Rigid Body"
                    and headers[3][index] == name
                    and headers[5][index] == "Position"
                    and headers[6][index] == axis
                )
                for axis in ("X", "Y", "Z")
            ]
        frames, times, camera_times = [], [], []
        positions = {name: [] for name in columns}
        for row in reader:
            if not row or not row[0].strip():
                continue
            frames.append(int(row[0]))
            times.append(float(row[1]))
            camera_times.append(int(row[2]))
            for name, indices in columns.items():
                point = tuple(float(row[index]) for index in indices)
                if not all(math.isfinite(value) for value in point):
                    raise ValueError(f"{member}: non-finite position at frame {frames[-1]}")
                positions[name].append(point)
    if len(times) != int(metadata["FrameCount"]):
        raise ValueError(f"{member}: frame count mismatch")
    rate = float(metadata["FrameRate"])
    for index in range(1, len(times)):
        if frames[index] != frames[index - 1] + 1:
            raise ValueError(f"{member}: missing or reordered frames")
        if not math.isclose(times[index] - times[index - 1], 1.0 / rate, abs_tol=1e-6):
            raise ValueError(f"{member}: irregular sample clock")
    return Recording(member, metadata, frames, times, camera_times, positions)


def endpoint(positions, samples):
    return tuple(statistics.median(point[axis] for point in positions[-samples:]) for axis in range(3))


def speed_trace(times, positions, half_window=5):
    speeds = []
    for index in range(len(times)):
        left = max(0, index - half_window)
        right = min(len(times), index + half_window + 1)
        local_times = [moment - times[index] for moment in times[left:right]]
        slopes = [
            statistics.linear_regression(local_times, [point[axis] for point in positions[left:right]]).slope
            for axis in range(3)
        ]
        speeds.append(math.sqrt(sum(value * value for value in slopes)))
    return speeds


def endpoint_band_stop(times, positions, tolerance_mm=2.0, hold_s=0.3):
    sample_interval = statistics.median(right - left for left, right in zip(times, times[1:]))
    tail_samples = max(3, round(0.5 / sample_interval))
    if len(times) < 2 * tail_samples:
        return None
    target = endpoint(positions, tail_samples)
    distances = [math.dist(point, target) for point in positions]
    outside = [
        index for index, value in enumerate(distances)
        if value > tolerance_mm and not math.isclose(value, tolerance_mm, rel_tol=1e-9, abs_tol=1e-9)
    ]
    if not outside:
        return None
    index = outside[-1] + 1
    if index >= len(times) or times[-1] - times[index] < hold_s - 1e-9:
        return None
    return {
        "index": index,
        "frame": index + 1,
        "time_s": times[index],
        "endpoint_mm": target,
        "residual_mm": distances[index],
        "observed_stable_s": times[-1] - times[index],
    }


def stop_candidate(times, positions, tolerance_mm=2.0, speed_mm_s=10.0, hold_s=0.3):
    band_stop = endpoint_band_stop(times, positions, tolerance_mm, hold_s)
    if band_stop is None:
        return None
    sample_interval = statistics.median(right - left for left, right in zip(times, times[1:]))
    speeds = speed_trace(times, positions)
    hold_intervals = math.ceil(hold_s / sample_interval - 1e-9)
    for index in range(band_stop["index"], len(times) - hold_intervals):
        if all(
            value <= speed_mm_s or math.isclose(value, speed_mm_s, rel_tol=1e-9, abs_tol=1e-9)
            for value in speeds[index:index + hold_intervals + 1]
        ):
            return {
                "index": index,
                "frame": index + 1,
                "time_s": times[index],
                "endpoint_mm": band_stop["endpoint_mm"],
                "residual_mm": math.dist(positions[index], band_stop["endpoint_mm"]),
                "speed_mm_s": speeds[index],
                "observed_stable_s": times[-1] - times[index],
            }
    return None


def percentile(values, percentage):
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[percentage - 1]


def delay_ms(stops):
    first, second = stops["Rigid1"], stops["Rigid2"]
    return round(1000 * (second["time_s"] - first["time_s"]), 6) if first and second else None


def describe(recording, tolerance_mm=2.0, speed_mm_s=10.0, hold_s=0.3):
    sample_interval = statistics.median(right - left for left, right in zip(recording.times, recording.times[1:]))
    tail_samples = round(0.5 / sample_interval)
    description = {
        "file": recording.name,
        "metadata": recording.metadata,
        "samples": len(recording.times),
        "duration_s": recording.times[-1] - recording.times[0],
        "first_time_s": recording.times[0],
        "last_time_s": recording.times[-1],
        "sample_period_ms": round(1000 * sample_interval, 6),
        "camera_timestamp_monotonic": all(right > left for left, right in zip(recording.camera_times, recording.camera_times[1:])),
        "camera_relative_error_us_assuming_microseconds": max(
            abs(value - recording.camera_times[0] - round((moment - recording.times[0]) * 1_000_000))
            for moment, value in zip(recording.times, recording.camera_times)
        ),
    }
    for name, positions in recording.positions.items():
        final_position = endpoint(positions, tail_samples)
        initial_position = endpoint(positions[:tail_samples], tail_samples)
        tail_distances = [math.dist(point, final_position) for point in positions[-tail_samples:]]
        speeds = speed_trace(recording.times, positions)
        description[name] = {
            "travel_mm": round(math.dist(initial_position, final_position), 3),
            "delta_xyz_mm": [round(final - initial, 3) for initial, final in zip(initial_position, final_position)],
            "tail_radius_p95_mm": round(percentile(tail_distances, 95), 4),
            "tail_radius_max_mm": round(max(tail_distances), 4),
            "tail_peak_to_peak_xyz_mm": [
                round(max(point[axis] for point in positions[-tail_samples:]) - min(point[axis] for point in positions[-tail_samples:]), 4)
                for axis in range(3)
            ],
            "tail_speed_p95_mm_s": round(percentile(speeds[-tail_samples:], 95), 3),
            "peak_speed_mm_s": round(max(speeds), 3),
        }
    dominant_axis = max(range(3), key=lambda axis: abs(description["Rigid1"]["delta_xyz_mm"][axis]))
    description["optical_direction"] = "XYZ"[dominant_axis] + ("+" if description["Rigid1"]["delta_xyz_mm"][dominant_axis] > 0 else "-")
    description["band_stops"] = {
        name: endpoint_band_stop(recording.times, positions, tolerance_mm, hold_s)
        for name, positions in recording.positions.items()
    }
    description["strict_stops"] = {
        name: stop_candidate(recording.times, positions, tolerance_mm, speed_mm_s, hold_s)
        for name, positions in recording.positions.items()
    }
    description["endpoint_band_delay_ms"] = delay_ms(description["band_stops"])
    description["strict_delay_ms"] = delay_ms(description["strict_stops"])
    description["band_sensitivity"] = []
    description["candidate_sensitivity"] = []
    for tolerance in (0.5, 1.0, 2.0, 3.0):
        band_stops = {
            name: endpoint_band_stop(recording.times, positions, tolerance, hold_s)
            for name, positions in recording.positions.items()
        }
        description["band_sensitivity"].append({"tolerance_mm": tolerance, "delay_ms": delay_ms(band_stops)})
        for velocity in (5.0, 10.0, 15.0, 20.0):
            first = stop_candidate(recording.times, recording.positions["Rigid1"], tolerance, velocity, hold_s)
            second = stop_candidate(recording.times, recording.positions["Rigid2"], tolerance, velocity, hold_s)
            description["candidate_sensitivity"].append({
                "tolerance_mm": tolerance,
                "speed_mm_s": velocity,
                "point1_stop_s": first["time_s"] if first else None,
                "point2_stop_s": second["time_s"] if second else None,
                "delay_ms": round(1000 * (second["time_s"] - first["time_s"]), 3) if first and second else None,
            })
    return description


def summarise(results, field):
    values = [result[field] for result in results if result[field] is not None]
    return {
        "valid_files": len(values),
        "total_files": len(results),
        "mean_ms": statistics.mean(values) if values else None,
        "median_ms": statistics.median(values) if values else None,
        "min_ms": min(values) if values else None,
        "max_ms": max(values) if values else None,
    }


def formatted(value, digits=2):
    return "未确认" if value is None else f"{value:.{digits}f}"


def render_chart(times, traces, title, unit, limits, marks, horizontal=None):
    width, height = 800, 270
    left, top, plot_width, plot_height = 66, 42, 712, 178
    start, end, low, high = limits
    svg = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {width} {height}", "role": "img", "aria-label": title})
    ET.SubElement(svg, "title").text = title
    def label(horizontal_position, vertical_position, text, **attributes):
        element = ET.SubElement(svg, "text", {"x": str(horizontal_position), "y": str(vertical_position), "font-size": "12", "fill": "#25323a", **attributes})
        element.text = text
    def project(moment, value):
        return left + (moment - start) * plot_width / (end - start), top + (high - value) * plot_height / (high - low)
    label(left, 20, title, **{"font-size": "15"})
    for index in range(6):
        moment = start + (end - start) * index / 5
        horizontal_position, unused_vertical = project(moment, low)
        ET.SubElement(svg, "line", {"x1": str(horizontal_position), "x2": str(horizontal_position), "y1": str(top), "y2": str(top + plot_height), "stroke": "#e2e6e8"})
        label(horizontal_position, top + plot_height + 20, f"{moment:.2f}", **{"text-anchor": "middle"})
    for index in range(5):
        value = low + (high - low) * index / 4
        unused_horizontal, vertical_position = project(start, value)
        ET.SubElement(svg, "line", {"x1": str(left), "x2": str(left + plot_width), "y1": str(vertical_position), "y2": str(vertical_position), "stroke": "#e2e6e8"})
        label(left - 8, vertical_position + 4, f"{value:.1f}", **{"text-anchor": "end"})
    if horizontal is not None:
        unused_horizontal, vertical_position = project(start, horizontal)
        ET.SubElement(svg, "line", {"x1": str(left), "x2": str(left + plot_width), "y1": str(vertical_position), "y2": str(vertical_position), "stroke": "#757575", "stroke-dasharray": "4 4"})
    for values, colour in traces:
        points = []
        for moment, value in zip(times, values):
            if start <= moment <= end:
                horizontal_position, vertical_position = project(moment, min(high, max(low, value)))
                points.append(f"{horizontal_position:.2f},{vertical_position:.2f}")
        ET.SubElement(svg, "polyline", {"points": " ".join(points), "fill": "none", "stroke": colour, "stroke-width": "1.5"})
    for moment, colour in marks:
        if moment is not None and start <= moment <= end:
            horizontal_position, unused_vertical = project(moment, low)
            ET.SubElement(svg, "line", {"x1": str(horizontal_position), "x2": str(horizontal_position), "y1": str(top), "y2": str(top + plot_height), "stroke": colour, "stroke-width": "1.6", "stroke-dasharray": "6 3"})
    label(left, height - 10, f"纵轴：{unit}；横轴：CSV Time / 秒；超出纵轴范围的曲线在边界截示")
    return ET.tostring(svg, encoding="unicode")


def write_outputs(recordings, results, args):
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    summaries = {field: summarise(results, field) for field in ("endpoint_band_delay_ms", "strict_delay_ms")}
    grouped = {
        direction: {field: summarise([item for item in results if item["optical_direction"] == direction], field) for field in summaries}
        for direction in sorted({item["optical_direction"] for item in results})
    }
    payload = {
        "source_archive": str(args.archive.resolve()),
        "sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "source_method": "https://cwjgfm21di.feishu.cn/wiki/V5Tjw1SnBi1Odtkv0s5c3M21nih?fromScene=spaceOverview",
        "method_revision": 163,
        "referenced_method": "https://cwjgfm21di.feishu.cn/wiki/KLylwq6x1iyTBIkF8FscQ9lpnld",
        "referenced_method_revision": 228,
        "point_mapping": {"point1": "Rigid1", "point2": "Rigid2", "physical_roles_confirmed": False},
        "user_context": "用户说明 PICO/Oli 运动接口跟随不理想，终点抖动属于真实运行现象；本分析保留全部位置样本，不假定抖动只是测量噪声。接口是否为根因未单独验证。",
        "parameters": {"position_tolerance_mm": args.tolerance_mm, "speed_threshold_mm_s": args.speed_mm_s, "hold_s": args.hold_s, "endpoint_window_s": 0.5, "velocity_window_samples": 11},
        "summaries": summaries,
        "by_optical_direction": grouped,
        "results": results,
    }
    (output / "分析结果.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output / "逐次停止时差.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["文件", "光学主方向", "样本数", "记录末时刻_s", "点一终点稳定时刻_s", "点二终点稳定时刻_s", "终点稳定时差_ms", "点一低速停止时刻_s", "点二低速停止时刻_s", "低速停止时差_ms", "点一稳定后剩余_s", "点二稳定后剩余_s", "点二尾段抖动最大_mm", "状态"])
        for result in results:
            band, strict = result["band_stops"], result["strict_stops"]
            get_value = lambda stops, name, field: stops[name][field] if stops[name] else "未确认"
            writer.writerow([
                result["file"], result["optical_direction"], result["samples"], result["last_time_s"],
                get_value(band, "Rigid1", "time_s"), get_value(band, "Rigid2", "time_s"), result["endpoint_band_delay_ms"],
                get_value(strict, "Rigid1", "time_s"), get_value(strict, "Rigid2", "time_s"), formatted(result["strict_delay_ms"], 0),
                get_value(band, "Rigid1", "observed_stable_s"), get_value(band, "Rigid2", "observed_stable_s"),
                result["Rigid2"]["tail_radius_max_mm"], "两种判据均有估计" if result["strict_delay_ms"] is not None else "终点稳定可估计；低速停止未确认",
            ])
    band_summary, strict_summary = summaries["endpoint_band_delay_ms"], summaries["strict_delay_ms"]
    lines = [
        "# 两测点停止时差分析", "", "## 结论", "",
        f"按点一=Rigid1、点二=Rigid2，终点 {args.tolerance_mm:g} mm 容差下，{band_summary['valid_files']}/{len(results)} 组可计算到位稳定时差，平均 **{band_summary['mean_ms']:.1f} ms**，范围 **{band_summary['min_ms']:.0f}～{band_summary['max_ms']:.0f} ms**。",
        f"增加局部拟合速度不超过 {args.speed_mm_s:g} mm/s、连续 {args.hold_s:g} 秒的检查后，{strict_summary['valid_files']}/{len(results)} 组能给出低速停止参考；已确认组均值 **{strict_summary['mean_ms']:.2f} ms**。未确认组不补零，也不剔除后宣称全部通过。", "",
        "这两栏是不同的操作性判据。方法文档没有规定停止位置/速度阈值，上述参数是本次离线分析选择，不是研发批准的验收标准；因此这些是停止延时估计，不是无条件唯一的物理停止时刻。主结果允许终点容差内的小幅残余抖动，不能据此宣称机械臂绝对静止。", "",
        "用户补充：PICO 与 Oli 当前运动控制接口跟随不理想，机械臂抖动是实际运行现象。本分析据此保留全部原始位置和波动，不将其作为异常点删除；该解释是用户提供的现场背景，不等于已经独立证明了接口根因。", "",
        "## 与测试方法的对应", "",
        f"- [原测试报告]({payload['source_method']})，修订号 163，原有结果用 50% 位移时差；这里没有复用那些旧数据。",
        f"- [报告引用的测试方法]({payload['referenced_method']})，修订号 228，第 5 条定义停止延时为两参考点到达停止状态的时间差。",
        "- 本次有符号结果 = 点二停止时刻 - 点一停止时刻；未取绝对值。正数表示点二晚停。",
        "- CSV 只标记 Rigid1/Rigid2，未注明哪个靶球贴在人手/手柄、哪个贴在机械臂。按你的点一/点二说法暂作上述映射，物理角色仍需贴标记录确认；相反映射会反转时差符号。",
        "- 本次全部动作的 Rigid1 主变化轴是光学坐标 Z，正反各 5 次；没有外参，不能直接称为机器人坐标 Z，也不能据此补出 X/Y 四个方向或六方向最大延迟。",
        "- 文档中的旧机器人编号和固件不自动代表此 ZIP 的设备；CSV 没有记录本次机器人 SN、固件、网络及遥操模式。", "",
        "## 数据与停止判据", "",
        f"- 压缩包包含 {len(recordings)} 份 CSV，共 {sum(len(recording.times) for recording in recordings)} 帧，位置为 mm；表头类型为 Rigid Body，只读取 Rigid1/Rigid2 的 Position XYZ，没有把附属标记点当作第二测点。",
        "- 每份 CSV 使用共同的 Time 列，采样率 100 Hz、周期 10 ms；实际行数与 FrameCount 一致，帧号连续、时间无缺口，两点坐标均为有限数。",
        "- 停止时刻是各文件内 Time 秒数，不是一个文件结束后减另一个文件开始。CameraSyncTime 为同帧公共字段；若按微秒解释，其与 Time 的相对偏差见 JSON，不用于跨设备时钟推断。",
        "- 每个点的最终参考位置取末尾 0.5 秒的 XYZ 分量中位数。起点参考同样取最初 0.5 秒中位数，仅用于位移方向和图中归一化。",
        f"- 终点稳定：找到该点最后一次超出最终参考位置 {args.tolerance_mm:g} mm 球形容差之后的第一帧；此后直到记录结束都在容差内，且至少观察 {args.hold_s:g} 秒。这不等于数学上严格零速度，也不能保证记录结束之后继续静止。",
        f"- 低速停止：在已进入上述终点稳定段后，三维速度不超过 {args.speed_mm_s:g} mm/s，连续至少 {args.hold_s:g} 秒。停止时刻回标为这一合格段的第一帧，不额外把确认等待时间加进延迟。",
        "- 速度用 11 点中心窗口、约 100 ms 跨度的各轴局部直线拟合，再取三维范数；原始坐标未滤波，时间轴没有平移。该离线估计使用未来样本，不适合直接作为实时停机控制算法。",
        "- 100 Hz 事件时间量化为 10 ms。中心拟合的时间支撑约 ±50 ms；这不是实测误差上界，容差、真实残余运动和测量误差会带来更大的判据不确定性。局部拟合还可能抵消高频往返分量，因此低速参考不能证明抖动消失。不要将三位小数误读为毫秒以下精度。", "",
        "## 逐次结果", "",
        "| 文件 | 光学方向 | 点一终点稳定/s | 点二终点稳定/s | 到位稳定差/ms | 点一低速停/s | 点二低速停/s | 低速停止差/ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        band, strict = result["band_stops"], result["strict_stops"]
        stop_time = lambda stops, name: formatted(stops[name]["time_s"] if stops[name] else None)
        lines.append(f"| {result['file']} | {result['optical_direction']} | {stop_time(band,'Rigid1')} | {stop_time(band,'Rigid2')} | {formatted(result['endpoint_band_delay_ms'],0)} | {stop_time(strict,'Rigid1')} | {stop_time(strict,'Rigid2')} | {formatted(result['strict_delay_ms'],0)} |")
    lines += ["", "## 按光学方向汇总", "", "| 方向 | 到位稳定有效数 | 平均/ms | 最小～最大/ms | 低速停止有效数 | 已确认均值/ms |", "| --- | --- | --- | --- | --- | --- |"]
    for direction, groups in grouped.items():
        band, strict = groups["endpoint_band_delay_ms"], groups["strict_delay_ms"]
        lines.append(f"| {direction} | {band['valid_files']}/{band['total_files']} | {formatted(band['mean_ms'])} | {formatted(band['min_ms'],0)}～{formatted(band['max_ms'],0)} | {strict['valid_files']}/{strict['total_files']} | {formatted(strict['mean_ms'])} |")
    lines += [
        "", "## 终点残余抖动", "",
        "以下统计末尾 0.5 秒的原始三维位置，没有删除抖动样本。最大偏离是相对该段 XYZ 中位数的距离；各轴峰峰值是该段最大值减最小值。这些数值不等于丢包、延迟或已确认的测量噪声。", "",
        "| 文件 | 点一最大偏离/mm | 点二最大偏离/mm | 点二 X/Y/Z 峰峰值/mm |", "| --- | --- | --- | --- |",
    ]
    for result in results:
        amplitudes = " / ".join(formatted(value, 3) for value in result["Rigid2"]["tail_peak_to_peak_xyz_mm"])
        lines.append(f"| {result['file']} | {formatted(result['Rigid1']['tail_radius_max_mm'],3)} | {formatted(result['Rigid2']['tail_radius_max_mm'],3)} | {amplitudes} |")
    lines += [
        "", "## 阈值敏感性与未确认项", "",
        "点一和点二的末尾抖动并不相同。2 mm 相当于点一约 199 mm 行程的约 1%；它是明确选择的分析容差，不是文件提供的设备分辨率。", "",
        "下表均保持 0.3 秒确认长度。只改变速度阈值的结果是补充敏感性分析，不替换未确认的严格结果。", "",
        "| 文件 | 仅1mm终点差/ms | 仅2mm终点差/ms | 仅3mm终点差/ms | 2mm+5mm/s | 2mm+10mm/s | 2mm+15mm/s | 2mm+20mm/s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        bands = {item["tolerance_mm"]: item["delay_ms"] for item in result["band_sensitivity"]}
        speeds = {item["speed_mm_s"]: item["delay_ms"] for item in result["candidate_sensitivity"] if item["tolerance_mm"] == 2.0}
        values = [bands[radius] for radius in (1.0, 2.0, 3.0)] + [speeds[speed] for speed in (5.0, 10.0, 15.0, 20.0)]
        lines.append("| " + result["file"] + " | " + " | ".join(formatted(value, 0) for value in values) + " |")
    lines += [
        "", "- 若低速判据显示未确认，含义是现有记录中没有足够长的合格段，不是无限延迟或 0 ms。",
        "- 小容差会将更多末端微调/振荡纳入停稳时间。按用户现场说明，本报告不把这些波动直接当成测量噪声滤除；若需分离设备测量误差，仍应做独立静止标定并结合现场视频。",
        "- 这份数据只能估计两可见测点的运动停止差，包含控制、滤波、执行器与机械动态；不能拆成纯网络传输延迟。",
        "- 正式验收前应确认点位物理映射、停止容差/速度阈值、确认时长和完整方向范围；本报告不直接判产品 PASS/FAIL。", "",
        "## 查看和复算", "",
        "- [逐次停止时差.csv](逐次停止时差.csv)：中文表头，可用表格软件打开。",
        "- [停止时差图表.html](停止时差图表.html)：每个文件的完整归一化轨迹和停止附近的距离、速度图；只读静态报告，无远程请求。",
        "- [分析结果.json](分析结果.json)：时间、帧号、参数、方向统计、终段波动与敏感性完整数据。",
        "- [离线分析脚本](../scripts/analyze_teleop_stops.py)：直接读取 ZIP，不解压执行、不连接机器人。", "",
        "```bash",
        "python scripts/analyze_teleop_stops.py --self-test",
        "python scripts/analyze_teleop_stops.py /path/to/teleop.zip --output-dir ./new_stop_analysis --tolerance-mm 2 --speed-mm-s 10 --hold-s 0.3",
        "```", "",
        f"原始 ZIP SHA-256：`{payload['sha256']}`。原 ZIP 未修改；分析目录必须是新目录，脚本拒绝覆盖既有结果。",
    ]
    (output / "停止延迟分析.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sections = []
    colours = ("#007e87", "#c04b28")
    for recording, result in zip(recordings, results):
        tail_samples = round(0.5 / (result["sample_period_ms"] / 1000))
        progress, distance_traces, velocities, marks = [], [], [], []
        for name, colour in zip(("Rigid1", "Rigid2"), colours):
            positions = recording.positions[name]
            initial, final = endpoint(positions[:tail_samples], tail_samples), endpoint(positions, tail_samples)
            displacement = [last - first for first, last in zip(initial, final)]
            denominator = sum(value * value for value in displacement)
            values = [sum((point[axis] - initial[axis]) * displacement[axis] for axis in range(3)) / denominator if denominator else 0 for point in positions]
            progress.append((values, colour))
            distance_traces.append(([math.dist(point, final) for point in positions], colour))
            velocities.append((speed_trace(recording.times, positions), colour))
            stop = result["band_stops"][name]
            marks.append((stop["time_s"] if stop else None, colour))
        available_times = [moment for moment, colour in marks if moment is not None]
        zoom_start = max(recording.times[0], min(available_times or [recording.times[-1] - 1]) - 0.5)
        zoom_end = min(recording.times[-1], max(available_times or [recording.times[-1]]) + 1.0)
        sections.append(
            f"<section><h2>{html.escape(recording.name)} <small>光学 {result['optical_direction']}</small></h2>"
            f"<p>终点稳定差：<b>{formatted(result['endpoint_band_delay_ms'],0)} ms</b>；低速停止差：<b>{formatted(result['strict_delay_ms'],0)}{' ms' if result['strict_delay_ms'] is not None else ''}</b>。"
            f"点一/点二终点稳定时刻：{formatted(marks[0][0])} / {formatted(marks[1][0])} 秒。</p>"
            + render_chart(recording.times, progress, "全程位移进度（各自起终点归一化，不平移时间）", "行程比例", (recording.times[0], recording.times[-1], -0.05, 1.10), marks)
            + "<div class='paired'>"
            + render_chart(recording.times, distance_traces, "停止附近：距各自终点的三维距离", "mm", (zoom_start, zoom_end, 0, max(8, args.tolerance_mm * 3)), marks, args.tolerance_mm)
            + render_chart(recording.times, velocities, "停止附近：局部拟合三维速度", "mm/s", (zoom_start, zoom_end, 0, max(30, args.speed_mm_s * 3)), marks, args.speed_mm_s)
            + "</div></section>"
        )
    html_document = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>两测点停止时差分析</title><style>
body{font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#25323a;background:#f6f8f8;margin:0;letter-spacing:0;line-height:1.65}
main{max-width:1240px;margin:auto;padding:24px}h1{font-size:26px}h2{font-size:20px}small{font-size:14px;font-weight:normal}
section{border-top:1px solid #c9d2d5;padding:18px 0}svg{display:block;width:100%;height:auto;background:white}.paired{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.first{color:#007e87}.second{color:#c04b28}p{overflow-wrap:anywhere}@media(max-width:760px){main{padding:12px}.paired{grid-template-columns:1fr}}
</style><main><h1>两测点停止时差分析</h1>""" + (
        f"<p>终点容差 {args.tolerance_mm:g} mm；低速阈值 {args.speed_mm_s:g} mm/s；确认长度 {args.hold_s:g} 秒。"
        f"终点稳定时差均值 {band_summary['mean_ms']:.1f} ms；低速结果 {strict_summary['valid_files']}/{len(results)} 组可确认。</p>"
        "<p><b class='first'>青色：点一 Rigid1</b>；<b class='second'>橙色：点二 Rigid2</b>。竖虚线为终点容差稳定时刻；灰横虚线为对应阈值，不表示零速度。"
        "坐标只为显示各自归一化，原始数据时间轴未移动。物理靶球对应关系需现场确认，结果不是纯网络延迟。</p>"
        "<p>100 Hz / 10 ms 采样。原始位置抖动全部保留；局部拟合低速不等于完全静止。低速列未确认时不补零；敏感性和算法解释见同目录 Markdown 与 CSV。</p>"
    ) + "".join(sections) + "</main></html>"
    (output / "停止时差图表.html").write_text(html_document, encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "summaries": summaries, "by_optical_direction": grouped}, ensure_ascii=False, indent=2))


class StopDetectionTests(unittest.TestCase):
    def test_known_delay(self):
        times = [index / 100 for index in range(1, 401)]
        first = [(min(moment, 2.0) * 100, 0, 0) for moment in times]
        second = [(max(0, min(moment - 0.15, 2.0)) * 100, 0, 0) for moment in times]
        first_stop = stop_candidate(times, first)
        second_stop = stop_candidate(times, second)
        self.assertAlmostEqual(second_stop["time_s"] - first_stop["time_s"], 0.15)

    def test_unfinished_movement_is_not_a_stop(self):
        times = [index / 100 for index in range(1, 401)]
        positions = [(moment * 100, 0, 0) for moment in times]
        self.assertIsNone(stop_candidate(times, positions))

    def test_later_excursion_invalidates_early_stop(self):
        times = [index / 100 for index in range(1, 501)]
        positions = [(min(moment, 2.0) * 100, 0, 0) for moment in times]
        positions[350] = (220, 0, 0)
        self.assertGreater(stop_candidate(times, positions)["time_s"], 3.5)

    def test_position_frame_rotation_preserves_stop(self):
        times = [index / 100 for index in range(1, 401)]
        positions = [(min(moment, 2.0) * 100, 0, 0) for moment in times]
        rotated = [(12, -point[0] + 7, 23) for point in positions]
        self.assertEqual(stop_candidate(times, positions)["index"], stop_candidate(times, rotated)["index"])

    def test_no_motion_has_no_stop_event(self):
        times = [index / 100 for index in range(1, 401)]
        positions = [(0, 0, 0) for unused in times]
        self.assertIsNone(endpoint_band_stop(times, positions))
        self.assertIsNone(stop_candidate(times, positions))

    def test_short_final_plateau_is_rejected(self):
        times = [index / 100 for index in range(1, 211)]
        positions = [(min(moment, 2.0) * 100, 0, 0) for moment in times]
        self.assertIsNone(endpoint_band_stop(times, positions))

    def test_stop_delay_keeps_its_sign(self):
        self.assertEqual(delay_ms({"Rigid1": {"time_s": 2.0}, "Rigid2": {"time_s": 1.8}}), -200)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--tolerance-mm", type=float, default=2.0)
    parser.add_argument("--speed-mm-s", type=float, default=10.0)
    parser.add_argument("--hold-s", type=float, default=0.3)
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(StopDetectionTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if args.archive is None:
        parser.error("archive is required")
    if not all(math.isfinite(value) and value > 0 for value in (args.tolerance_mm, args.speed_mm_s, args.hold_s)):
        parser.error("analysis thresholds must be finite and positive")
    recordings, results = [], []
    with zipfile.ZipFile(args.archive) as archive:
        if sum(member.file_size for member in archive.infolist()) > 200_000_000:
            raise ValueError("Archive exceeds the offline analysis size budget")
        for member in sorted(archive.namelist()):
            if member.endswith(".csv"):
                recording = read_recording(archive, member)
                result = describe(recording, args.tolerance_mm, args.speed_mm_s, args.hold_s)
                recordings.append(recording)
                results.append(result)
    if not results:
        raise ValueError("Archive contains no CSV recordings")
    if args.output_dir is not None:
        write_outputs(recordings, results, args)
    else:
        for result in results:
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()