from models.robot_profile import L04_PROFILE, OLI_PROFILE
from services.calibrate_service import CalibrateService
from services.dance_service import DanceService
from ui.panels.calibrate_panel import CalibratePanel
from ui.panels.control_panel import ControlPanel
from ui.panels.dance_library_panel import DanceLibraryPanel
from workers.mcp_worker import McpWorker


def _worker(profile):
    return McpWorker(
        "ws://10.192.1.2:5000",
        "HU_L04_01_091",
        allowed_tools=profile.allowed_tools,
    )


def test_l04_control_panel_keeps_queries_and_locks_commands(qtbot):
    worker = _worker(L04_PROFILE)
    panel = ControlPanel(worker)
    qtbot.addWidget(panel)

    panel.apply_profile(L04_PROFILE)

    assert panel._tool_buttons["audio_get_wakeup"].isEnabled()
    assert panel._tool_buttons["get_action_library_status"].isEnabled()
    assert not panel._tool_buttons["prepare"].isEnabled()
    assert not panel._tool_buttons["audio_wakeup_enable"].isEnabled()
    assert not panel._tool_buttons["led_green"].isEnabled()


def test_oli_control_panel_preserves_existing_commands(qtbot):
    worker = _worker(OLI_PROFILE)
    panel = ControlPanel(worker)
    qtbot.addWidget(panel)

    panel.apply_profile(OLI_PROFILE)

    assert panel._tool_buttons["prepare"].isEnabled()
    assert panel._tool_buttons["audio_wakeup_enable"].isEnabled()


def test_l04_dance_library_is_query_only(qtbot, monkeypatch):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    monkeypatch.setattr(service, "get_count", lambda _name: 0)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)

    panel.apply_profile(L04_PROFILE)
    panel._populate_motions([{"motion_name_en": "wave", "motion_name_cn": "挥手"}])

    assert panel.refresh_dances_btn.isEnabled()
    assert panel.refresh_motions_btn.isEnabled()
    assert not panel.motion_engine_btn.isEnabled()
    assert not panel.tabs.isTabEnabled(2)
    assert not panel._motion_cards["wave"].isEnabled()


def test_l04_calibration_panel_is_locked(qtbot):
    worker = _worker(L04_PROFILE)
    service = CalibrateService(worker)
    panel = CalibratePanel(service)
    qtbot.addWidget(panel)

    panel.apply_profile(L04_PROFILE)

    assert not panel.ws_calibrate_btn.isEnabled()
    assert not panel.bl_connect_btn.isEnabled()
    assert "尚未完成真机验证" in panel.result_log.toPlainText()