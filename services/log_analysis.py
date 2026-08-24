"""Profile-aware, UI-independent robot log analysis."""
from dataclasses import dataclass, field
from datetime import datetime
import re


OLI_SLAVE_MOTOR_MAP = {
    2: (15, "waist pitch", False), 3: (14, "waist roll", False), 4: (13, "waist yaw", True),
    5: (18, "Left Shoulder pitch", False), 6: (19, "Left Shoulder roll", False),
    7: (20, "Left Shoulder yaw", False), 8: (21, "Left Elbow", False),
    9: (22, "Left Wrist yaw", False), 10: (23, "Left Wrist roll", False), 11: (24, "Left Wrist pitch", True),
    13: (1, "Left Hip pitch", False), 14: (2, "Left Hip roll", False), 15: (3, "Left Hip yaw", False),
    16: (4, "Left Knee", False), 17: (5, "Left Ankle pitch", False), 18: (6, "Left Ankle roll", True),
    19: (16, "Head yaw", False), 20: (17, "Head pitch", True),
    22: (25, "Right Shoulder pitch", False), 23: (26, "Right Shoulder roll", False),
    24: (27, "Right Shoulder yaw", False), 25: (28, "Right Elbow", False),
    26: (29, "Right Wrist yaw", False), 27: (30, "Right Wrist roll", False), 28: (31, "Right Wrist pitch", True),
    29: (7, "Right Hip pitch", False), 30: (8, "Right Hip roll", False), 31: (9, "Right Hip yaw", False),
    32: (10, "Right Knee", False), 33: (11, "Right Ankle pitch", False), 34: (12, "Right Ankle roll", True),
}

CONTROLLER_STATE_MAP = {
    "ZeroTorque": "零力矩", "MotionLibrary": "动作库", "Mimic": "舞蹈",
    "Walk": "拟人行走", "Damping": "阻尼", "IkStand": "站立",
    "IkStand,GroundDetection": "站立和离地检测", "GroundDetection": "离地检测",
    "LieDown": "躺着", "SitDown": "装箱姿势", "StandSit": "坐姿",
    "MotionEdit": "动作编排", "SitStand": "坐姿起身", "LieSit": "躺姿起身",
    "TeleopArmInit": "遥操作初始化", "TeleopArmInit,LBWalk": "遥操作初始化",
    "LBWalk,TeleopArmExit": "遥操作初始化退出姿态", "LBWalk": "LBWalk",
    "": "无控制器（校零模式）",
}

PRODUCT_PREFIXES = (
    ("HU_D04_01", "oli", "Oli"),
    ("HU_L04_01", "hu_l04_01", "Luna L04"),
)
PRODUCT_NAMES = {
    "oli": "Oli",
    "hu_l04_01": "Luna L04",
}


def _product_from_sn(sn: str) -> tuple[str, str]:
    normalized = sn.upper()
    for prefix, profile_key, product_name in PRODUCT_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "_"):
            return profile_key, product_name
    return "unknown", "未知型号"


@dataclass(frozen=True)
class LogEvent:
    category: str
    title: str
    detail: str
    timestamp: str
    line_number: int
    severity: str = "info"


@dataclass(frozen=True)
class LogFinding:
    code: str
    category: str
    title: str
    detail: str
    severity: str
    first_timestamp: str
    last_timestamp: str
    count: int
    evidence_lines: tuple[int, ...]
    resolved: bool | None = None

    def as_event(self) -> LogEvent:
        count_suffix = f"（{self.count} 次）" if self.count > 1 else ""
        return LogEvent(
            self.category,
            f"{self.title}{count_suffix}",
            self.detail,
            self.first_timestamp,
            self.evidence_lines[0] if self.evidence_lines else 1,
            self.severity,
        )


@dataclass
class LogAnalysis:
    profile_key: str = "unknown"
    product_name: str = "未知型号"
    sn: str = "未知"
    versions: dict[str, str] = field(default_factory=lambda: {
        "pms": "-", "ecm": "-", "ctrl": "-", "motor": "-",
    })
    current_controller: str = "-"
    controller_switch_count: int = 0
    events: list[LogEvent] = field(default_factory=list)
    findings: list[LogFinding] = field(default_factory=list)


def _timestamp(line: str) -> str:
    match = re.search(
        r"(?:^|\[)(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d{3})?)",
        line,
    ) or re.search(r"(\d{2}:\d{2}:\d{2}(?:\.\d{3})?)", line)
    return match.group(1) if match else "--:--:--"


def _duration_seconds(start: str, end: str) -> float | None:
    try:
        return (
            datetime.strptime(end, "%Y-%m-%d %H:%M:%S.%f")
            - datetime.strptime(start, "%Y-%m-%d %H:%M:%S.%f")
        ).total_seconds()
    except ValueError:
        return None


def _within_seconds(start: str, end: str, maximum: float) -> bool:
    duration = _duration_seconds(start, end)
    return duration is not None and 0 <= duration <= maximum


def _summarize_motor_versions(lines: list[str]) -> str:
    versions = []
    motor_ids = set()
    for line in lines:
        if "name:motor_version" not in line:
            continue
        message = line.split("msg:", 1)[-1].split(" sn:", 1)[0]
        for motor_id, version in re.findall(r"(\d+)\s*:\s*([0-9A-Za-z._-]+)", message):
            motor_ids.add(int(motor_id))
            versions.append(version)
    unique_versions = sorted(set(versions))
    if len(unique_versions) == 1:
        return f"全部 {unique_versions[0]}（共 {len(motor_ids)} 个驱动器）"
    if unique_versions:
        return ", ".join(unique_versions)
    return "-"


def _finding(
    code: str,
    category: str,
    title: str,
    detail: str,
    severity: str,
    matches: list[tuple[int, str]],
    count: int | None = None,
    resolved: bool | None = None,
) -> LogFinding:
    evidence = tuple(dict.fromkeys(line_number for line_number, _ in matches))
    return LogFinding(
        code=code,
        category=category,
        title=title,
        detail=detail,
        severity=severity,
        first_timestamp=_timestamp(matches[0][1]),
        last_timestamp=_timestamp(matches[-1][1]),
        count=count if count is not None else len(matches),
        evidence_lines=evidence[:8],
        resolved=resolved,
    )


def _analyze_oli(lines: list[str]) -> list[LogFinding]:
    findings = []
    indexed = list(enumerate(lines, start=1))
    master_starts = [(number, line) for number, line in indexed if "Limx EtherCAT Master" in line]
    topology_failures = [(number, line) for number, line in indexed if "Ec application check topology fail" in line]
    f10d_exits = [(number, line) for number, line in indexed if "错误代码是 0xf10d" in line]
    mismatches = []
    for number, line in indexed:
        match = re.search(
            r"Slave mismatch at slaveid (\d+), expected slave productcode = (0x[0-9a-fA-F]+), but now is (0x[0-9a-fA-F]+)",
            line,
        )
        if match:
            mismatches.append((number, line, match.groups()))
    branches = []
    for number, line in indexed:
        match = re.search(
            r"Expected branch (\d+) \(([^)]+)\) has parent_slaveid = (\d+) parent_port = (\d+), but now parent_slaveid = (\d+) parent_port = (\d+)",
            line,
        )
        if match:
            branches.append((number, line, match.groups()))

    missing_branch = [
        (number, line) for number, line in indexed
        if re.search(r"\[[^]]+\] 的所有电机找不到", line)
    ]
    has_topology_evidence = bool(
        topology_failures or mismatches or branches or missing_branch
    )
    if has_topology_evidence:
        details = []
        evidence = []
        if mismatches:
            number, line, values = mismatches[0]
            slave, expected, actual = values
            details.append(f"Slave {slave} 产品码不符（期望 {expected.lower()}，实际 {actual.lower()}）")
            evidence.append((number, line))
        if branches:
            number, line, values = branches[0]
            _, name, expected_slave, expected_port, actual_slave, actual_port = values
            details.append(
                f"{name} 分支期望 Slave {expected_slave} port{expected_port}，"
                f"实际 Slave {actual_slave} port{actual_port}"
            )
            evidence.append((number, line))
        if missing_branch:
            branch_name = re.search(r"\[([^]]+)\] 的所有电机找不到", missing_branch[0][1]).group(1)
            details.append(f"{branch_name} 分支全部电机未被识别")
            evidence.append(missing_branch[0])
        details.append(
            f"主站启动 {len(master_starts)} 次，拓扑检查失败 {len(topology_failures)} 次，"
            f"以 0xf10d 退出 {len(f10d_exits)} 次"
        )
        evidence.extend(topology_failures[:1])
        evidence.extend(f10d_exits[:1])
        evidence.extend(f10d_exits[-1:])
        ordered_evidence = sorted(dict.fromkeys(evidence))
        findings.append(_finding(
            "OLI_ETHERCAT_TOPOLOGY_RESTART_LOOP",
            "拓扑",
            "EtherCAT 拓扑识别失败并反复重启",
            "；".join(details),
            "error",
            ordered_evidence,
            count=max(len(topology_failures), len(f10d_exits), 1),
            resolved=False,
        ))
    elif f10d_exits:
        evidence = f10d_exits[:1] + f10d_exits[-1:]
        findings.append(_finding(
            "OLI_ETHERCAT_MASTER_EXIT_LOOP",
            "主站",
            "EtherCAT 主站异常退出",
            f"主站启动 {len(master_starts)} 次，以 0xf10d 退出 {len(f10d_exits)} 次；"
            "日志中没有足够拓扑证据，不能归类为拓扑识别失败",
            "error",
            evidence,
            count=len(f10d_exits),
            resolved=False,
        ))
    return findings


def _analyze_luna(lines: list[str]) -> list[LogFinding]:
    findings = []
    indexed = list(enumerate(lines, start=1))
    power_off = [
        (number, line) for number, line in indexed
        if "motor power turn off" in line
    ]
    power_on = [
        (number, line) for number, line in indexed
        if "motor power turn on" in line
    ]
    offline = []
    enabled = []
    for number, line in indexed:
        match = re.search(r"\[ethercat\] state = \d+, motor (\d+) offline", line)
        if match:
            offline.append((number, line, int(match.group(1))))
        match = re.search(r"\[ethercat\] motor (\d+) enabled", line)
        if match:
            enabled.append((number, line, int(match.group(1))))

    for power_off_index, off_event in enumerate(power_off):
        next_off_line = (
            power_off[power_off_index + 1][0]
            if power_off_index + 1 < len(power_off) else len(lines) + 1
        )
        on_event = next((
            item for item in power_on
            if off_event[0] < item[0] < next_off_line
        ), None)
        offline_window = [
            item for item in offline
            if off_event[0] < item[0] < (on_event[0] if on_event else next_off_line)
        ]
        if not offline_window:
            continue

        enabled_window = []
        if on_event:
            enabled_window = [
                item for item in enabled
                if on_event[0] < item[0] < next_off_line
                and _within_seconds(_timestamp(on_event[1]), _timestamp(item[1]), 120.0)
            ]
        offline_motors = sorted({motor for _, _, motor in offline_window})
        enabled_motors = sorted({motor for _, _, motor in enabled_window})
        resolved = bool(on_event and offline_motors == enabled_motors)
        first_offline = (offline_window[0][0], offline_window[0][1])
        last_recovery = (
            (enabled_window[-1][0], enabled_window[-1][1])
            if enabled_window else (on_event or first_offline)
        )
        duration = _duration_seconds(
            _timestamp(first_offline[1]), _timestamp(last_recovery[1])
        )
        duration_text = (
            f"，离线到{'全部恢复' if resolved else '最后证据'}约 {duration:.1f} 秒"
            if duration is not None else ""
        )
        motor_range = (
            f"电机{offline_motors[0]}-{offline_motors[-1]}"
            if offline_motors == list(range(offline_motors[0], offline_motors[-1] + 1))
            else "电机" + ",".join(str(item) for item in offline_motors)
        )
        missing = sorted(set(offline_motors) - set(enabled_motors))
        evidence = [off_event, first_offline]
        if on_event:
            evidence.append(on_event)
        if enabled_window:
            evidence.append(last_recovery)
        if resolved:
            code = "LUNA_MOTOR_POWER_CYCLE"
            title = "电机上下电期间暂时离线并恢复"
            detail = (
                f"PMS 关闭电机电源后 {motor_range} 离线；重新上电后全部 enabled"
                f"{duration_text}。日志未包含 link_status/错误计数器 dump，不能据此定位硬件断点"
            )
            severity = "warning"
        else:
            code = "LUNA_MOTOR_POWER_INTERRUPTION"
            title = "电机下电后未检测到完整恢复"
            missing_text = ",".join(str(item) for item in missing) or "未知"
            detail = (
                f"PMS 关闭电机电源后 {motor_range} 离线；未检测到完整恢复，"
                f"缺少 enabled 证据的电机: {missing_text}{duration_text}。"
                "日志未包含 link_status/错误计数器 dump，不能据此定位硬件断点"
            )
            severity = "error"
        findings.append(_finding(
            code, "电源", title, detail, severity, evidence,
            count=1, resolved=resolved,
        ))

    hand_warnings = [
        (number, line) for number, line in indexed if "No hand detected" in line
    ]
    if hand_warnings:
        channels = sorted(set(re.findall(r"can\d+", "\n".join(line for _, line in hand_warnings))))
        findings.append(_finding(
            "LUNA_HAND_NOT_DETECTED",
            "外设",
            "未检测到灵巧手",
            f"未在 {','.join(channels) or 'CAN'} 检测到灵巧手；需结合当前硬件配置判断是否为预期",
            "warning",
            hand_warnings,
        ))

    voice_failures = [
        (number, line) for number, line in indexed if "Voice config HTTP GET failed" in line
    ]
    if voice_failures:
        codes = sorted(set(
            f"curl={curl_code}, http={http_code}"
            for _, line in voice_failures
            for curl_code, http_code in re.findall(r"curl_code=(\d+), http_code=(\d+)", line)
        ))
        findings.append(_finding(
            "LUNA_VOICE_CONFIG_HTTP_FAILED",
            "网络",
            "语音配置服务请求失败",
            "；".join(codes) or "语音配置 HTTP GET 失败",
            "warning",
            voice_failures,
        ))
    return findings


def analyze_log(content: str, profile_key: str | None = None) -> LogAnalysis:
    lines = content.splitlines()
    sn_match = re.search(
        r"\bsn\s*:\s*([A-Za-z0-9_]+)", content, flags=re.IGNORECASE,
    )
    sn = sn_match.group(1) if sn_match else "未知"
    detected_profile_key, detected_product_name = _product_from_sn(sn)
    effective_profile_key = profile_key or detected_profile_key
    product_name = PRODUCT_NAMES.get(effective_profile_key, detected_product_name)
    result = LogAnalysis(
        profile_key=effective_profile_key,
        product_name=product_name,
        sn=sn,
    )

    current_controller = "-"
    processed_links = set()
    warned_motors = set()
    motor_version_lines = []
    for line_number, line in enumerate(lines, start=1):
        timestamp = _timestamp(line)
        if "name:pms_version" in line:
            match = re.search(r"msg:([^ ]+)", line)
            if match:
                result.versions["pms"] = match.group(1)
        elif "name:ecm_version" in line:
            match = re.search(r"msg:([^ ]+)", line)
            if match:
                result.versions["ecm"] = match.group(1)
        software_match = re.search(r"(robot-(?:hu|luna)-r-[0-9A-Za-z._-]+)", line)
        if software_match and result.versions["ctrl"] == "-":
            result.versions["ctrl"] = software_match.group(1)
        if "name:motor_version" in line:
            motor_version_lines.append(line)

        if "ability_running" in line and re.search(r"\b(?:msg|message)\s*:", line):
            match = re.search(
                r"\b(?:msg|message)\s*:\s*([A-Za-z0-9_,/-]*)", line,
            )
            if match:
                state = CONTROLLER_STATE_MAP.get(match.group(1).strip(), match.group(1).strip())
                if state != current_controller:
                    result.controller_switch_count += 1
                    result.events.append(LogEvent(
                        "控制器", state,
                        f"从 {current_controller}" if current_controller != "-" else "初始化",
                        timestamp, line_number, "info",
                    ))
                    current_controller = state

        if effective_profile_key == "oli":
            link_match = re.search(
                r"slave\s*=\s*(\d+).*?link_status\s*=\s*(0x[0-9a-fA-F]+)", line,
            )
            if link_match:
                slave_id = int(link_match.group(1))
                status = link_match.group(2).lower()
                motor_info = OLI_SLAVE_MOTOR_MAP.get(slave_id)
                if motor_info and status != "0x5a37":
                    motor_id, part_name, is_last = motor_info
                    if not (status == "0x5617" and is_last):
                        key = (slave_id, status)
                        if key not in processed_links:
                            processed_links.add(key)
                            result.events.append(LogEvent(
                                "通讯", f"Slave{slave_id} 通讯异常",
                                f"Motor{motor_id} {part_name} status={status}",
                                timestamp, line_number,
                                "error" if status == "0x0" else "warning",
                            ))

        warning_match = re.search(
            r"code:\s*65537.*?motor\s+(\d+)\s+MOTOR_WARNING.*?VOICE_PROMPT", line,
        )
        if warning_match:
            motor_id = int(warning_match.group(1))
            if motor_id not in warned_motors:
                warned_motors.add(motor_id)
                result.events.append(LogEvent(
                    "电机", f"电机{motor_id} 语音警告", "可能堵转或过速",
                    timestamp, line_number, "warning",
                ))

        if re.search(r"recv\s+power\s+mtv\s+state\s*:\s*off", line, re.IGNORECASE):
            result.events.append(LogEvent(
                "电源", "驱动器下电", "所有关节电机断电", timestamp, line_number, "warning",
            ))
        elif re.search(r"recv\s+power\s+mtv\s+state\s*:\s*on", line, re.IGNORECASE):
            result.events.append(LogEvent(
                "电源", "驱动器上电", "电机预充电完成", timestamp, line_number, "success",
            ))

    result.versions["motor"] = _summarize_motor_versions(motor_version_lines)
    result.current_controller = current_controller
    if effective_profile_key == "oli":
        result.findings.extend(_analyze_oli(lines))
    elif effective_profile_key == "hu_l04_01":
        result.findings.extend(_analyze_luna(lines))
    result.events.extend(finding.as_event() for finding in result.findings)
    result.events.sort(key=lambda item: item.line_number)
    return result