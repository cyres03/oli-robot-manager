from PyQt6.QtWidgets import QMessageBox

from models.robot_profile import L04_PROFILE, OLI_PROFILE
from services.calibrate_service import CalibrateService
from services.dance_service import DanceService
from ui.panels.calibrate_panel import CalibratePanel
from ui.panels.control_panel import ControlPanel
from ui.panels.dance_library_panel import DanceLibraryPanel
from ui.dialogs.message_dialog import AppMessageBox
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


def test_switching_from_l04_to_oli_restores_existing_commands(qtbot):
    worker = _worker(L04_PROFILE)
    panel = ControlPanel(worker)
    qtbot.addWidget(panel)
    actions = []
    panel.action_requested.connect(
        lambda name, arguments: (
            actions.append((name, arguments)),
            worker.call_tool(name, arguments),
        )
    )

    panel.apply_profile(L04_PROFILE)
    assert not panel._tool_buttons["prepare"].isEnabled()

    worker.update_target(
        "HU_D04_01_062",
        OLI_PROFILE.allowed_tools,
        profile_key=OLI_PROFILE.key,
    )
    panel.apply_profile(OLI_PROFILE)
    panel.update_robot_status({
        "robot_status": "Damped",
        "ability": "?",
        "mode": "?",
    })

    for button_key in (
        "prepare",
        "standup",
        "safe_stop",
        "set_walk_mode",
        "set_motion_engine_1",
        "audio_wakeup_enable",
        "led_green",
    ):
        button = panel._tool_buttons[button_key]
        assert button.isEnabled()
        assert "尚未开放" not in button.toolTip()

    for button_key in ("sit_down", "lie_down", "straight_walk"):
        button = panel._tool_buttons[button_key]
        assert not button.isEnabled()
        assert "吊装保护" in button.toolTip()

    for button_key in ("damping", "zero_torque"):
        button = panel._tool_buttons[button_key]
        assert not button.isEnabled()
        assert "需先勾选" in button.toolTip()

    panel._tool_buttons["prepare"].click()
    assert actions == [("prepare", {})]
    assert worker._pending_requests[-1][:2] == ("prepare", {})


def test_clearing_profile_locks_all_control_commands(qtbot):
    worker = _worker(OLI_PROFILE)
    panel = ControlPanel(worker)
    qtbot.addWidget(panel)

    panel.apply_profile(OLI_PROFILE)
    assert panel._tool_buttons["prepare"].isEnabled()

    panel.apply_profile(None)

    assert all(not button.isEnabled() for button in panel._tool_buttons.values())
    assert all(
        "尚未开放" in button.toolTip()
        for button in panel._tool_buttons.values()
    )


def test_l04_dance_library_allows_actions_only_in_walk(qtbot, monkeypatch):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    monkeypatch.setattr(service, "get_count", lambda _name: 0)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)

    panel.apply_profile(L04_PROFILE)
    panel._populate_dances([{"rc_mapping": "wakawaka", "name": "哇卡哇卡"}])
    panel._populate_motions([{"motion_name_en": "wave", "motion_name_cn": "挥手"}])

    assert panel.refresh_dances_btn.isEnabled()
    assert panel.refresh_motions_btn.isEnabled()
    assert not panel.motion_engine_btn.isEnabled()
    assert not panel.tabs.isTabEnabled(2)
    assert not panel.tabs.isTabEnabled(3)
    assert not panel._dance_cards["wakawaka"].isEnabled()
    assert not panel._motion_cards["wave"].isEnabled()

    panel.update_robot_status({"robot_status": "Walk"})

    assert panel._dance_cards["wakawaka"].isEnabled()
    assert panel._motion_cards["wave"].isEnabled()
    assert panel._motion_cards["wave"].repeat_btn.isEnabled()
    assert panel._motion_cards["wave"].repeat_btn.text() == "×5"
    assert not panel.tabs.isTabEnabled(3)

    panel.repeat_count_spin.setValue(8)

    assert panel._motion_cards["wave"].repeat_btn.text() == "×8"


def test_l04_repeat_action_confirms_and_binds_selected_count(qtbot, monkeypatch):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    monkeypatch.setattr(service, "get_count", lambda _name: 0)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)
    panel.apply_profile(L04_PROFILE)
    panel._populate_motions([{"motion_name_en": "wave", "motion_name_cn": "挥手"}])
    panel.update_robot_status({"robot_status": "Walk"})
    confirmations = []
    authorizations = []
    executions = []
    monkeypatch.setattr(
        panel,
        "_confirm_luna_action",
        lambda action_type, name, **_kwargs: (
            confirmations.append((action_type, name)) or True
        ),
    )
    monkeypatch.setattr(
        service,
        "authorize_next_action",
        lambda action_type, name, repeat_count=1: (
            authorizations.append((action_type, name, repeat_count)) or True
        ),
    )
    monkeypatch.setattr(
        service,
        "execute_motion_repeat",
        lambda name, times, delay_ms, completion_timeout=None: executions.append(
            (name, times, delay_ms, completion_timeout)
        ),
    )
    panel.repeat_count_spin.setValue(8)

    panel._motion_cards["wave"].repeat_btn.click()

    assert confirmations == [("连续原子动作（8 次）", "wave")]
    assert authorizations == [("motion", "wave", 8)]
    assert executions == [("wave", 8, 2000, None)]


def test_l04_salute_uses_same_sync_repeat_controls_as_other_motions(qtbot, monkeypatch):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    monkeypatch.setattr(service, "get_count", lambda _name: 0)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)
    panel.apply_profile(L04_PROFILE)
    panel._populate_motions([{
        "motion_name_en": "Salute",
        "motion_name_cn": "敬礼",
        "duration": 6,
    }])
    panel.update_robot_status({"robot_status": "Walk"})
    card = panel._motion_cards["Salute"]
    assert card.execute_btn.isEnabled()
    assert card.repeat_btn.isEnabled()
    assert card.repeat_btn.text() == "×5"


def test_motion_duration_sets_sync_timeout_for_single_and_repeat(qtbot, monkeypatch):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    monkeypatch.setattr(service, "get_count", lambda _name: 0)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)
    panel.apply_profile(L04_PROFILE)
    panel._populate_motions([{
        "motion_name_en": "Long motion",
        "motion_name_cn": "长动作",
        "duration": 20,
    }])
    panel.update_robot_status({"robot_status": "Walk"})
    monkeypatch.setattr(panel, "_confirm_luna_action", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "authorize_next_action", lambda *_args, **_kwargs: True)
    single_calls = []
    repeat_calls = []
    monkeypatch.setattr(
        service,
        "execute_motion",
        lambda name, completion_timeout=None: single_calls.append(
            (name, completion_timeout)
        ),
    )
    monkeypatch.setattr(
        service,
        "execute_motion_repeat",
        lambda name, times, delay_ms, completion_timeout=None: repeat_calls.append(
            (name, times, delay_ms, completion_timeout)
        ),
    )
    card = panel._motion_cards["Long motion"]

    card.execute_btn.click()
    card.repeat_btn.click()

    assert single_calls == [("Long motion", 50.0)]
    assert repeat_calls == [("Long motion", 5, 2000, 50.0)]


def test_l04_menu_enables_sync_motions_but_not_dances(qtbot, monkeypatch):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    monkeypatch.setattr(service, "get_count", lambda _name: 0)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)
    panel.apply_profile(L04_PROFILE)
    panel._populate_dances([{"rc_mapping": "wakawaka", "name": "哇卡哇卡"}])
    panel._populate_motions([{"motion_name_en": "Salute", "motion_name_cn": "敬礼"}])

    panel.update_robot_status({"robot_status": "Menu"})

    assert not panel._dance_cards["wakawaka"].isEnabled()
    assert panel._motion_cards["Salute"].isEnabled()
    assert "同步接口结束后将确认返回 Walk" in panel.action_status_label.text()


def test_l04_status_refresh_does_not_reenable_actions_while_running(qtbot, monkeypatch):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    monkeypatch.setattr(service, "get_count", lambda _name: 0)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)
    panel.apply_profile(L04_PROFILE)
    panel._populate_motions([{"motion_name_en": "wave", "motion_name_cn": "挥手"}])
    panel.update_robot_status({"robot_status": "Walk"})

    panel._on_action_state_changed(True, "连续动作执行中: wave (1/5)")
    panel.update_robot_status({"robot_status": "Walk"})
    panel.apply_profile(L04_PROFILE)

    assert not panel._motion_cards["wave"].isEnabled()
    assert not panel.repeat_count_spin.isEnabled()
    assert panel.action_status_label.text() == "连续动作执行中: wave (1/5)"


def test_l04_action_confirmation_blocks_non_walk_and_can_approve_walk(
    qtbot,
    monkeypatch,
):
    worker = _worker(L04_PROFILE)
    service = DanceService(worker)
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)
    panel.apply_profile(L04_PROFILE)
    warnings = []
    monkeypatch.setattr(
        AppMessageBox,
        "warning",
        lambda *_args: warnings.append(_args[2]),
    )

    assert panel._confirm_luna_action("原子动作", "Nod") is False
    assert "仅允许在 Walk 状态执行" in warnings[-1]

    panel.update_robot_status({"robot_status": "Walk"})
    monkeypatch.setattr(
        AppMessageBox,
        "exec",
        lambda _self: QMessageBox.StandardButton.Yes,
    )

    assert panel._confirm_luna_action("原子动作", "Nod") is True


def test_l04_calibration_panel_is_locked(qtbot):
    worker = _worker(L04_PROFILE)
    service = CalibrateService(worker)
    panel = CalibratePanel(service)
    qtbot.addWidget(panel)

    panel.apply_profile(L04_PROFILE)

    assert not panel.ws_calibrate_btn.isEnabled()
    assert not panel.bl_connect_btn.isEnabled()
    assert "尚未完成真机验证" in panel.result_log.toPlainText()