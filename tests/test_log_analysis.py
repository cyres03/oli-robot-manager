from services.log_analysis import analyze_log
from ui.panels.log_analyzer_panel import LogAnalyzerPanel


OLI_LOG = """\
2026-08-22 15:26:09.078 I/robotlogger: VERSION: "robot-hu-r-2.3.15.20260702141838"
2026-08-22 15:26:10.000 I/rbtmgr: name:pms_version level:0 code:0 msg:2.2.8 sn:HU_D04_01_048
2026-08-22 15:26:10.010 I/rbtmgr: name:ecm_version level:0 code:0 msg:1.1.18 sn:HU_D04_01_048
2026-08-22 15:26:10.080 I/ethercat: Limx EtherCAT Master V1.1.18.
2026-08-22 15:26:11.189 I/ethercat: Slave mismatch at slaveid 9, expected slave productcode = 0x3831002, but now is 0x4685432
2026-08-22 15:26:11.552 W/ethercat: Expected branch 0 (hu_waist) has parent_slaveid = 1 parent_port = 3, but now parent_slaveid = 1 parent_port = 1
2026-08-22 15:26:11.552 E/ethercat: [hu_waist] 的所有电机找不到
2026-08-22 15:26:11.552 E/ethercat: Ec application check topology fail
2026-08-22 15:26:11.558 E/ethercat: 进程退出, 因为错误代码是 0xf10d, 错误消息是 No text found..
2026-08-22 15:26:23.613 I/ethercat: Limx EtherCAT Master V1.1.18.
2026-08-22 15:26:24.718 I/ethercat: Slave mismatch at slaveid 9, expected slave productcode = 0x3831002, but now is 0x4685432
2026-08-22 15:26:25.069 W/ethercat: Expected branch 0 (hu_waist) has parent_slaveid = 1 parent_port = 3, but now parent_slaveid = 1 parent_port = 1
2026-08-22 15:26:25.069 E/ethercat: [hu_waist] 的所有电机找不到
2026-08-22 15:26:25.069 E/ethercat: Ec application check topology fail
2026-08-22 15:26:25.074 E/ethercat: 进程退出, 因为错误代码是 0xf10d, 错误消息是 No text found..
2026-08-22 15:26:32.357 I/elevation_map: VERSION: "robot-hu-r-2.0.29.20250930191907"
"""


LUNA_LOG = """\
2026-08-23 15:14:08.131 I/rbtmgr: name:pms_version level:0 code:0 msg:1.2.3 sn:HU_L04_01_091
2026-08-23 15:14:08.132 I/rbtmgr: name:ecm_version level:0 code:0 msg:2.0.6 sn:HU_L04_01_091
2026-08-23 15:14:08.133 I/node: VERSION: "robot-luna-r-1.2.12.20260821201520"
2026-08-23 15:14:09.525 W/end_effector: [ZW] No hand detected on can0 at any baud level.
2026-08-23 15:14:09.915 W/end_effector: [ZW] No hand detected on can1 at any baud level.
2026-08-23 15:36:49.406 I/pms_node: <t-power> motor power turn off
2026-08-23 15:36:49.738 W/ethercat: [ethercat] state = 2, motor 1 offline
2026-08-23 15:36:49.743 W/ethercat: [ethercat] state = 2, motor 2 offline
2026-08-23 15:36:50.000 E/ethercat: slave = 2, link_status = 0x0, ret = -3
2026-08-23 15:36:54.268 I/pms_node: <t-power> motor power turn on
2026-08-23 15:36:58.580 I/ethercat: [ethercat] motor 1 enabled
2026-08-23 15:36:58.606 I/ethercat: [ethercat] motor 2 enabled
2026-08-23 15:36:57.562 I/rbtmgr: name:motor_version level:0 code:0 msg:1: 1.1.16; 2: 1.1.16; sn:HU_L04_01_091
2026-08-23 15:38:44.417 W/signaling: Voice config HTTP GET failed url=https://example.invalid, curl_code=28, http_code=0
2026-08-23 15:39:44.417 W/signaling: Voice config HTTP GET failed url=https://example.invalid, curl_code=28, http_code=0
"""


def _finding(analysis, code):
    return next(item for item in analysis.findings if item.code == code)


def test_oli_topology_restart_loop_is_aggregated():
    analysis = analyze_log(OLI_LOG)
    finding = _finding(analysis, "OLI_ETHERCAT_TOPOLOGY_RESTART_LOOP")

    assert analysis.profile_key == "oli"
    assert analysis.sn == "HU_D04_01_048"
    assert analysis.versions["ctrl"] == "robot-hu-r-2.3.15.20260702141838"
    assert finding.severity == "error"
    assert finding.count == 2
    assert finding.resolved is False
    assert "Slave 9 产品码不符" in finding.detail
    assert "hu_waist 分支期望 Slave 1 port3，实际 Slave 1 port1" in finding.detail
    assert "以 0xf10d 退出 2 次" in finding.detail


def test_luna_power_cycle_is_resolved_without_oli_mapping():
    analysis = analyze_log(LUNA_LOG)
    finding = _finding(analysis, "LUNA_MOTOR_POWER_CYCLE")

    assert analysis.profile_key == "hu_l04_01"
    assert analysis.versions["motor"] == "全部 1.1.16（共 2 个驱动器）"
    assert finding.resolved is True
    assert "电机1-2 离线" in finding.detail
    assert "不能据此定位硬件断点" in finding.detail
    assert not any(event.category == "通讯" for event in analysis.events)


def test_luna_peripheral_and_network_warnings_are_aggregated():
    analysis = analyze_log(LUNA_LOG)
    hand = _finding(analysis, "LUNA_HAND_NOT_DETECTED")
    voice = _finding(analysis, "LUNA_VOICE_CONFIG_HTTP_FAILED")

    assert hand.count == 2
    assert "can0,can1" in hand.detail
    assert voice.count == 2
    assert voice.detail == "curl=28, http=0"


def test_oli_link_mapping_is_only_used_for_oli():
    oli = analyze_log(
        "2026-01-01 00:00:00.000 I/x: sn:HU_D04_01_001\n"
        "2026-01-01 00:00:01.000 E/x: slave = 2, link_status = 0x0"
    )
    unknown = analyze_log(
        "2026-01-01 00:00:00.000 I/x: sn:HU_X99_01_001\n"
        "2026-01-01 00:00:01.000 E/x: slave = 2, link_status = 0x0"
    )

    assert any("Motor15" in event.detail for event in oli.events)
    assert not any(event.category == "通讯" for event in unknown.events)


def test_explicit_profile_identifies_log_without_sn():
    analysis = analyze_log(
        "2026-01-01 00:00:00.000 I/x: application started",
        profile_key="hu_l04_01",
    )

    assert analysis.product_name == "Luna L04"
    assert analysis.sn == "未知"


def test_log_panel_renders_profile_findings(qtbot):
    panel = LogAnalyzerPanel()
    qtbot.addWidget(panel)

    panel.analyze_text(OLI_LOG, "oli-sample.log")

    assert "Oli · HU_D04_01_048" in panel.file_label.text()
    assert panel.summary_labels["faults"].text() == "1 错误 / 0 告警 / 0 次切换"
    assert any("EtherCAT 拓扑识别失败" in event.title for event in panel._events)