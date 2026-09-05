import time
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from config import ROBOT_CONFIG
from models.robot_profile import L04_PROFILE, OLI_PROFILE
from ui.panels.acceptance_test_panel import (
    BEIJING_TIMEZONE,
    AcceptanceTestPanel,
    build_acceptance_checks,
    build_diagnostic_checks,
)


def _check(checks, key):
    return next(item for item in checks if item.key == key)


def test_l04_checks_use_companion_node_and_mark_mcp_na():
    checks = build_acceptance_checks(L04_PROFILE)

    assert _check(checks, "companion_ssh").tool == "guest@10.192.1.4"
    assert _check(checks, "cpu").target == "companion"
    assert _check(checks, "mcp").kind == "na"
    assert "mroswebvideo" in _check(checks, "camera").command


def test_oli_checks_preserve_perception_topology_and_mcp():
    checks = build_acceptance_checks(OLI_PROFILE)

    assert _check(checks, "companion_ssh").tool == "guest@10.192.1.3"
    assert _check(checks, "mcp").kind == "http"
    assert "lsusb" in _check(checks, "camera").command


def test_diagnostic_checks_add_identity_status_and_mros_without_writes():
    checks = build_diagnostic_checks(OLI_PROFILE)

    assert checks[0].key == "robot_info"
    assert checks[-1].key == "mros_services"
    assert "mrosservice list" in checks[-1].command
    all_commands = "\n".join(check.command for check in checks)
    for write_command in ("rm ", "reboot", "date -s", "set-timezone"):
        assert write_command not in all_commands


def test_mros_snapshot_counts_service_entries(qtbot):
    panel = AcceptanceTestPanel(profile=OLI_PROFILE)
    qtbot.addWidget(panel)
    check = _check(build_diagnostic_checks(OLI_PROFILE), "mros_services")

    passed, summary = panel._evaluate_ssh_output(
        check,
        "\x1b[32m* /joint/calibration [type: std_srvs/Trigger]\x1b[0m\n"
        "* /mission_engine/switch_state [type: std_srvs/SetString]\n",
    )

    assert passed is True
    assert summary == "读取到 2 个 mROS 服务"


def test_l04_panel_reports_mcp_as_not_applicable(qtbot):
    panel = AcceptanceTestPanel(profile=L04_PROFILE)
    qtbot.addWidget(panel)
    row = next(index for index, check in enumerate(panel.CHECKS) if check.key == "mcp")
    panel._pending = [row]

    panel._run_next_check()

    assert panel.check_table.item(row, 3).text() == "N/A"
    assert "N/A 1" in panel.summary_label.text()


def test_l04_camera_evaluation_accepts_visual_service(qtbot):
    panel = AcceptanceTestPanel(profile=L04_PROFILE)
    qtbot.addWidget(panel)
    camera = _check(panel.CHECKS, "camera")

    passed, summary = panel._evaluate_ssh_output(
        camera,
        "123 guest mroswebvideo --camera head\n124 guest GestureMrosNode",
    )

    assert passed is True
    assert summary == "视觉/相机服务=是"


def test_profile_switch_rebuilds_acceptance_rows(qtbot, monkeypatch):
    panel = AcceptanceTestPanel(profile=OLI_PROFILE)
    qtbot.addWidget(panel)
    monkeypatch.setattr(ROBOT_CONFIG, "active_profile", L04_PROFILE)

    panel.apply_profile(L04_PROFILE)

    mcp_row = next(index for index, check in enumerate(panel.CHECKS) if check.key == "mcp")
    assert panel.check_table.item(mcp_row, 2).text() == "该型号不支持"


def test_l04_time_failure_does_not_run_remote_fix(qtbot, monkeypatch):
    panel = AcceptanceTestPanel(profile=L04_PROFILE)
    qtbot.addWidget(panel)
    row = next(index for index, check in enumerate(panel.CHECKS) if check.key == "companion_time")
    check = panel.CHECKS[row]
    finished = []
    fixes = []
    monkeypatch.setattr(panel, "_finish_check", lambda *args: finished.append(args))
    monkeypatch.setattr(panel, "_run_time_fix", lambda *args, **kwargs: fixes.append((args, kwargs)))

    panel._on_time_checked(
        row,
        check,
        "TIME=2000-01-01 00:00:00 +0800\nZONE=Asia/Shanghai",
        (datetime.now(BEIJING_TIMEZONE), time.monotonic()),
    )

    assert finished
    assert finished[0][1] is False
    assert "仅检查，不自动校时" in finished[0][2]
    assert fixes == []


def test_profile_switch_rejects_late_acceptance_callback(qtbot):
    panel = AcceptanceTestPanel(profile=OLI_PROFILE)
    qtbot.addWidget(panel)
    old_generation = panel._profile_generation

    panel.apply_profile(L04_PROFILE)
    panel._run_if_current(
        old_generation,
        panel._on_robot_info_loaded,
        {"software_version": "stale-oli"},
    )

    assert panel.version_labels["software_version"].text() == "-"


def test_version_refresh_rejects_mismatched_robot_identity(qtbot, monkeypatch):
    panel = AcceptanceTestPanel(profile=OLI_PROFILE)
    qtbot.addWidget(panel)
    monkeypatch.setattr(ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")

    panel._on_robot_info_refresh_done(
        {
            "sn": "HU_D04_01_999",
            "software_version": "wrong-robot-version",
        },
        "HU_D04_01_075",
    )

    assert panel.version_labels["software_version"].text() == "-"
    assert "拒绝导入版本信息" in panel.detail_view.toPlainText()


def test_version_refresh_rejects_response_without_robot_identity(qtbot, monkeypatch):
    panel = AcceptanceTestPanel(profile=OLI_PROFILE)
    qtbot.addWidget(panel)
    monkeypatch.setattr(ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")

    panel._on_robot_info_refresh_done(
        {"software_version": "unverified-version"},
        "HU_D04_01_075",
    )

    assert panel.version_labels["software_version"].text() == "-"
    assert "无法识别" in panel.detail_view.toPlainText()


def test_profile_switch_rejects_late_beijing_time_callback(qtbot, monkeypatch):
    import ui.panels.acceptance_test_panel as acceptance_module

    workers = []

    class FakeBeijingTimeWorker(QObject):
        time_ready = pyqtSignal(object)
        failed = pyqtSignal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            workers.append(self)

        def start(self):
            pass

    panel = AcceptanceTestPanel(profile=OLI_PROFILE)
    qtbot.addWidget(panel)
    calls = []
    monkeypatch.setattr(acceptance_module, "BeijingTimeWorker", FakeBeijingTimeWorker)
    monkeypatch.setattr(panel, "_on_time_checked", lambda *args: calls.append(args))
    check = _check(panel.CHECKS, "main_time")

    panel._request_beijing_time(0, check, "old-output", verification=False)
    panel.apply_profile(L04_PROFILE)
    workers[0].time_ready.emit((datetime.now(BEIJING_TIMEZONE), time.monotonic()))

    assert calls == []


def test_profile_switch_clears_old_authorization_and_sudo_state(qtbot, monkeypatch):
    panel = AcceptanceTestPanel(profile=OLI_PROFILE)
    qtbot.addWidget(panel)
    check = _check(panel.CHECKS, "companion_time")
    panel._ssh_retry = (0, check, "HU_D04_01_001")
    panel._pending_time_fix = (
        0, check, "2026-01-01 00:00:00", "HU_D04_01_001",
    )
    finishes = []
    fixes = []
    monkeypatch.setattr(panel, "_finish_check", lambda *args: finishes.append(args))
    monkeypatch.setattr(panel, "_run_time_fix", lambda *args, **kwargs: fixes.append((args, kwargs)))

    panel.apply_profile(L04_PROFILE)
    panel.finish_ssh_authorization(True, "")
    panel.submit_sudo_password("old-password")

    assert panel._ssh_retry is None
    assert panel._pending_time_fix is None
    assert finishes == []
    assert fixes == []