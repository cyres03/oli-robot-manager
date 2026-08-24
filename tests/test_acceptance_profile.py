import time
from datetime import datetime

from config import ROBOT_CONFIG
from models.robot_profile import L04_PROFILE, OLI_PROFILE
from ui.panels.acceptance_test_panel import (
    BEIJING_TIMEZONE,
    AcceptanceTestPanel,
    build_acceptance_checks,
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