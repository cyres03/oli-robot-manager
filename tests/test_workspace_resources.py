import json

import pytest
import services.dance_service as dance_service_module

from models.robot_profile import (
    CapabilityState,
    L04_PROFILE,
    OLI_PROFILE,
    TRON2_PROFILE,
    RobotIdentity,
    RobotIdentityStatus,
)
from models.workspace import (
    CONNECTION_WORKSPACE,
    LUNA_WORKSPACE,
    OLI_WORKSPACE,
    TRON2_WORKSPACE,
    resolve_workspace,
)
from services.dance_service import DanceService, ResourceContext
from models.dance import DanceSequence, SequenceStep
from workers.mcp_worker import McpWorker


def _result(
    tool_name: str,
    content: dict,
    context: ResourceContext,
    resource_type: str = "dance",
) -> tuple[str, dict]:
    request_context = ResourceContext(
        context.profile_key,
        context.accid,
        context.firmware,
        resource_type,
    )
    return tool_name, {
        "success": True,
        "content": [json.dumps(content)],
        "_target_context": {
            "generation": 1,
            "accid": context.accid,
            "profile_key": context.profile_key,
            "request_context": request_context.to_dict(),
        },
    }


def test_workspace_registry_routes_products():
    assert resolve_workspace(OLI_PROFILE) is OLI_WORKSPACE
    assert resolve_workspace(L04_PROFILE) is LUNA_WORKSPACE
    assert resolve_workspace(TRON2_PROFILE) is TRON2_WORKSPACE
    assert resolve_workspace(None) is CONNECTION_WORKSPACE
    assert OLI_WORKSPACE.route("calibrate") is not None
    assert OLI_WORKSPACE.route("test_cases") is not None
    assert LUNA_WORKSPACE.route("calibrate") is None
    assert LUNA_WORKSPACE.route("log_analysis") is not None
    assert LUNA_WORKSPACE.route("test_cases") is not None
    assert OLI_PROFILE.capability("hand_fatigue") == CapabilityState.SUPPORTED
    assert L04_PROFILE.capability("hand_fatigue") == CapabilityState.SUPPORTED
    assert "hand_fatigue" not in L04_PROFILE.allowed_tools
    assert TRON2_WORKSPACE.default_route == "acceptance"
    assert TRON2_WORKSPACE.route("acceptance") is not None
    assert TRON2_WORKSPACE.route("log_analysis") is not None
    assert TRON2_WORKSPACE.route("controls") is None
    assert TRON2_WORKSPACE.route("dance_library") is None
    assert TRON2_WORKSPACE.route("test_cases") is None
    assert TRON2_WORKSPACE.route("health_check") is None
    assert TRON2_WORKSPACE.route("calibrate") is None


def test_sidebar_switches_product_navigation(qtbot):
    from ui.widgets.sidebar import Sidebar

    sidebar = Sidebar()
    qtbot.addWidget(sidebar)

    sidebar.apply_profile(L04_PROFILE)
    sidebar.apply_workspace(LUNA_WORKSPACE)
    assert sidebar._buttons["dance_library"].text().strip() == "Luna 资源库"
    assert sidebar._buttons["controls"].text().strip() == "状态与查询"
    assert sidebar._buttons["calibrate"].isHidden()
    assert not sidebar._buttons["log_analysis"].isHidden()

    sidebar.apply_profile(OLI_PROFILE)
    sidebar.apply_workspace(OLI_WORKSPACE)
    assert sidebar._buttons["dance_library"].text().strip() == "Oli 舞蹈与动作"
    assert not sidebar._buttons["calibrate"].isHidden()

    sidebar.apply_profile(TRON2_PROFILE)
    sidebar.apply_workspace(TRON2_WORKSPACE)
    assert sidebar._buttons["acceptance"].text().strip() == "TRON2 验收"
    assert sidebar._buttons["controls"].isHidden()
    assert sidebar._buttons["dance_library"].isHidden()
    assert sidebar._buttons["calibrate"].isHidden()
    assert sidebar._ssh_buttons[0].text().strip() == (
        "开发扩展电脑 (guest@10.192.1.4)"
    )
    assert not sidebar._ssh_buttons[0].isHidden()
    assert sidebar._ssh_buttons[1].isHidden()


def test_health_route_uses_product_specific_panel(qtbot, monkeypatch):
    from ui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window._active_workspace = LUNA_WORKSPACE
    indices = []
    window.stack = object()
    window.status_bar_widget = type("Status", (), {"setVisible": lambda *_: None})()
    window._switch_page = indices.append
    window.terminal = type("Terminal", (), {"append_log": lambda *_: None})()

    window._on_navigate("health_check")
    assert indices == [7]

    indices.clear()
    window._active_workspace = OLI_WORKSPACE
    window._on_navigate("health_check")
    assert indices == [3]


def test_tron2_does_not_load_hidden_dance_resources():
    from ui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    calls = []
    window._dance_service = type(
        "DanceServiceStub",
        (),
        {
            "load_dances": lambda _self: calls.append("dances"),
            "load_motions": lambda _self: calls.append("motions"),
        },
    )()

    window._load_profile_resources(TRON2_PROFILE)
    assert calls == []

    window._load_profile_resources(L04_PROFILE)
    assert calls == ["dances", "motions"]


def test_resource_switch_clears_views_and_rejects_old_response(qapp):
    worker = McpWorker("ws://robot", "HU_D04_01_001")
    service = DanceService(worker)
    dances = []
    motions = []
    service.dance_list_loaded.connect(lambda value: dances.append(value))
    service.motion_list_loaded.connect(lambda value: motions.append(value))
    oli_context = ResourceContext("oli", "HU_D04_01_001", "v1")
    luna_context = ResourceContext("hu_l04_01", "HU_L04_01_091", "v2")

    service.switch_resource_context(
        oli_context.profile_key, oli_context.accid, oli_context.firmware,
    )
    tool, result = _result("get_dances", {"dances": [{"name": "Oli舞蹈"}]}, oli_context)
    service._on_tool_result(tool, result)
    assert dances[-1] == [{"name": "Oli舞蹈"}]

    service.switch_resource_context(
        luna_context.profile_key, luna_context.accid, luna_context.firmware,
    )
    assert dances[-1] == []
    assert motions[-1] == []

    service._on_tool_result(tool, result)
    assert dances[-1] == []

    tool, result = _result("get_dances", {"dances": [{"name": "Luna舞蹈"}]}, luna_context)
    service._on_tool_result(tool, result)
    assert dances[-1] == [{"name": "Luna舞蹈"}]


def test_worker_drops_response_from_old_generation(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_D04_01_001")
    emitted = []
    worker.tool_result_ready.connect(lambda tool, result: emitted.append((tool, result)))
    monkeypatch.setattr(
        "workers.mcp_worker.RobotClient.call_tool",
        lambda self, tool, arguments: {"success": True, "content": ["[]"]},
    )
    pending = (
        "get_dances",
        {},
        worker.target_generation,
        {"accid": "old"},
        "ws://robot",
        "HU_D04_01_001",
        "oli",
    )

    worker.update_target("HU_L04_01_091", frozenset({"get_dances"}), profile_key="hu_l04_01")
    worker._execute_pending(pending)

    assert emitted == []


def test_queued_request_freezes_target_before_switch(qapp, monkeypatch):
    worker = McpWorker("ws://old", "HU_D04_01_001")
    worker.update_target(
        "HU_D04_01_001",
        frozenset({"get_dances"}),
        "ws://old",
        "oli",
    )
    worker.call_tool("get_dances", {})
    pending = worker._pending_requests.pop(0)
    calls = []

    class RecordingClient:
        def __init__(self, url, accid):
            calls.append((url, accid))

        def call_tool(self, name, arguments):
            return {"success": True, "content": ["[]"]}

    monkeypatch.setattr("workers.mcp_worker.RobotClient", RecordingClient)
    worker.update_target(
        "HU_L04_01_091",
        frozenset({"get_dances"}),
        "ws://new",
        "hu_l04_01",
    )

    worker._execute_pending(pending)

    assert calls == []


def test_late_action_response_does_not_mutate_new_workspace(qapp, monkeypatch):
    worker = McpWorker("ws://old", "HU_D04_01_001")
    worker.update_target(
        "HU_D04_01_001", frozenset({"execute_dance"}), "ws://old", "oli",
    )
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_001", "v1")
    old_generation = worker.target_generation
    increments = []
    monkeypatch.setattr(
        service, "_increment_count",
        lambda name, category: increments.append((name, category)) or 1,
    )
    service._pending_name = "old-dance"
    service._busy = True

    worker.update_target(
        "HU_L04_01_091", frozenset({"get_dances"}), "ws://new", "hu_l04_01",
    )
    service.switch_resource_context("hu_l04_01", "HU_L04_01_091", "v2")
    service._on_tool_result("execute_dance", {
        "success": True,
        "content": ["{}"],
        "_target_context": {
            "generation": old_generation,
            "accid": "HU_D04_01_001",
            "profile_key": "oli",
            "request_context": None,
        },
    })

    assert increments == []
    assert service._busy is True


def test_firmware_context_update_preserves_inflight_action(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    assert service.authorize_next_action("motion", "Nod") is True
    service.execute_motion("Nod")
    generation = worker.target_generation

    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v2")

    assert worker.target_generation == generation
    assert service._busy is True
    assert service.resource_context.firmware == "v2"


def test_target_switch_during_action_keeps_fail_closed_lock(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service._busy = True
    errors = []
    states = []
    service.error_occurred.connect(errors.append)
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )

    service.switch_resource_context("oli", "HU_D04_01_084", "v1")

    assert service._busy is True
    assert "旧目标状态无法确认" in errors[-1]
    assert states[-1] == (True, "目标已切换，旧机器人动作状态未确认")


def test_luna_service_rechecks_walk_before_queueing_action(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    errors = []
    service.error_occurred.connect(errors.append)

    service.update_robot_status({"robot_status": "ZeroTorque"})
    service.execute_motion("Nod")

    assert worker._pending_requests == []
    assert "仅允许在 Walk 或 Menu 状态执行该操作" in errors[-1]

    service.update_robot_status({"robot_status": "Walk"})
    service.execute_motion("Nod")

    assert worker._pending_requests == []
    assert "需要重新确认现场安全" in errors[-1]

    assert service.authorize_next_action("motion", "Nod") is True
    service.execute_motion("Nod")

    assert worker._pending_requests[-1][:2] == (
        "execute_motion",
        {"motion_name": "Nod"},
    )

    worker._pending_requests.clear()
    service.execute_motion("Nod")

    assert worker._pending_requests == []
    assert "当前已有舞蹈/动作在执行" in errors[-1]


def test_luna_service_allows_authorized_repeat_and_blocks_other_extended_paths(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    errors = []
    service.error_occurred.connect(errors.append)

    service.update_robot_status({"robot_status": "Walk"})
    service.execute_motion_repeat("Nod", times=5)
    assert worker._pending_requests == []
    assert "需要重新确认现场安全" in errors[-1]

    assert service.authorize_next_action("motion", "Nod", repeat_count=5) is True
    service.execute_motion_repeat("Nod", times=5, delay_ms=2000)

    assert worker._pending_requests[-1][:2] == (
        "execute_motion",
        {"motion_name": "Nod"},
    )
    assert service._repeat_motion_total == 5
    assert service._repeat_motion_remaining == 4

    worker._pending_requests.clear()
    service.stop_motion_repeat()
    service.execute_sequence(object())
    service.set_walk_velocity(0.1, 0, 0)
    service.set_motion_engine(1)

    assert worker._pending_requests == []
    assert errors[-3:] == [
        "Luna L04 尚未开放序列器",
        "Luna L04 尚未开放行走控制",
        "Luna L04 尚未开放手动动作库模式",
    ]


def test_luna_repeat_authorization_is_bound_to_count(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    errors = []
    service.error_occurred.connect(errors.append)

    assert service.authorize_next_action("motion", "Nod", repeat_count=5) is True
    service.execute_motion_repeat("Nod", times=6)

    assert worker._pending_requests == []
    assert "需要重新确认现场安全" in errors[-1]


def test_luna_repeat_runs_each_authorized_round_and_finishes(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    monkeypatch.setattr(service, "_increment_count", lambda *_args: 1)
    scheduled = []

    class ImmediateTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((delay_ms, callback))

    monkeypatch.setattr(dance_service_module, "QTimer", ImmediateTimer)
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )
    result = {
        "success": True,
        "content": [json.dumps({
            "response": "success",
            "notify": "success",
            "post_action": {
                "exit_motion_engine": "success",
                "set_walk_mode": "success",
                "verified_mode": "remote_control",
                "verified_state": "idle",
            },
        })],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_L04_01_084",
            "profile_key": "hu_l04_01",
            "request_context": None,
        },
    }

    assert service.authorize_next_action("motion", "Nod", repeat_count=2) is True
    service.execute_motion_repeat("Nod", times=2, delay_ms=2000)
    repeat_request_context = dict(service._repeat_request_context)
    result["_target_context"]["request_context"] = repeat_request_context
    worker._pending_requests.clear()
    service._robot_status = "Menu"

    service._on_tool_result("execute_motion", result)

    assert service._repeat_motion_done == 1
    assert service._robot_status == "Walk"
    assert len(scheduled) == 1
    assert scheduled[0][0] == 2000
    scheduled[0][1]()
    assert worker._pending_requests[-1][:2] == (
        "execute_motion",
        {"motion_name": "Nod"},
    )

    worker._pending_requests.clear()
    service._on_tool_result("execute_motion", result)

    assert service._busy is False
    assert service._repeat_motion_name == ""
    assert states[-1] == (False, "连续动作完成: Nod (2/2)")


def test_luna_repeat_does_not_resume_after_walk_is_lost_while_waiting(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service._repeat_motion_name = "Nod"
    service._repeat_motion_total = 5
    service._repeat_motion_remaining = 4
    service._repeat_motion_done = 1
    service._repeat_generation = 7
    service._busy = True

    service.update_robot_status({"robot_status": "Damped"})
    service.update_robot_status({"robot_status": "Walk"})
    service._send_next_repeat_motion(7)

    assert worker._pending_requests == []
    assert service._busy is False
    assert service._repeat_motion_name == ""


@pytest.mark.parametrize(
    "transient_status",
    ["?", "", "Action", "Remote", "MotionLibrary", "Mimic", "Stand", "Prepare"],
)
def test_luna_repeat_ignores_unknown_and_action_transient_statuses(
    qapp,
    transient_status,
):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    service._repeat_motion_name = "Nod"
    service._repeat_motion_total = 5
    service._repeat_motion_remaining = 4
    service._repeat_motion_done = 1
    service._repeat_motion_in_flight = True
    service._busy = True

    service.update_robot_status({"robot_status": transient_status})

    assert service._robot_status == "Walk"
    assert service._repeat_stop_requested is False
    assert service._repeat_motion_name == "Nod"
    assert service._busy is True


@pytest.mark.parametrize(
    "unsafe_status",
    ["Damped", "ZeroTorque", "DampedException", "ERROR_FALLOVER", "Fall_Detection"],
)
def test_luna_repeat_explicit_unsafe_status_stops_future_rounds(
    qapp,
    unsafe_status,
):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    service._repeat_motion_name = "Nod"
    service._repeat_motion_total = 5
    service._repeat_motion_remaining = 4
    service._repeat_motion_done = 1
    service._repeat_motion_in_flight = True
    service._repeat_request_context = {
        "kind": "repeat_motion",
        "generation": 1,
    }
    service._busy = True
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )

    service.update_robot_status({"robot_status": unsafe_status})

    assert service._repeat_stop_requested is True
    assert states[-1] == (
        True,
        f"安全状态 {unsafe_status} 触发停止: Nod (1/5)",
    )


def test_luna_repeat_does_not_resume_after_walk_is_lost_in_flight(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    monkeypatch.setattr(service, "_increment_count", lambda *_args: 1)
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )
    assert service.authorize_next_action("motion", "Nod", repeat_count=3) is True
    service.execute_motion_repeat("Nod", times=3)
    with worker._pending_lock:
        request_index = next(
            index
            for index, item in enumerate(worker._pending_requests)
            if item[0] == "execute_motion"
        )
        started_request = worker._pending_requests.pop(request_index)

    service.update_robot_status({"robot_status": "Damped"})
    service.update_robot_status({"robot_status": "Walk"})
    service._on_tool_result("execute_motion", {
        "success": True,
        "content": [json.dumps({
            "response": "success",
            "notify": "success",
            "post_action": {
                "exit_motion_engine": "success",
                "set_walk_mode": "success",
            },
        })],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_L04_01_084",
            "profile_key": "hu_l04_01",
            "request_context": started_request[3],
        },
    })

    assert worker._pending_requests == []
    assert service._busy is False
    assert service._repeat_motion_name == ""
    assert states[-1] == (False, "连续动作已停止: Nod (1/3)")


def test_luna_repeat_cancels_queued_round_when_walk_is_lost(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )
    assert service.authorize_next_action("motion", "Nod", repeat_count=3) is True
    service.execute_motion_repeat("Nod", times=3)

    service.update_robot_status({"robot_status": "Damped"})

    assert not any(
        item[0] == "execute_motion" for item in worker._pending_requests
    )
    assert service._busy is False
    assert service._repeat_motion_name == ""
    assert states[-1] == (False, "连续动作中止: Nod (0/3)")


def test_stale_repeat_timer_cannot_advance_a_new_batch(qapp):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    service.execute_motion_repeat("old", times=2, delay_ms=2000)
    old_generation = service._repeat_generation
    worker._pending_requests.clear()
    service._repeat_motion_in_flight = False
    service.stop_motion_repeat()

    service.execute_motion_repeat("new", times=3, delay_ms=2000)
    worker._pending_requests.clear()
    remaining = service._repeat_motion_remaining

    service._send_next_repeat_motion(old_generation)

    assert worker._pending_requests == []
    assert service._repeat_motion_name == "new"
    assert service._repeat_motion_remaining == remaining


def test_stopping_repeat_waits_for_inflight_round_and_does_not_schedule_next(
    qapp,
    monkeypatch,
):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    worker.update_target(
        "HU_D04_01_084",
        OLI_PROFILE.allowed_tools,
        "ws://robot",
        "oli",
    )
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    monkeypatch.setattr(service, "_increment_count", lambda *_args: 1)
    scheduled = []

    class CapturingTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((delay_ms, callback))

    monkeypatch.setattr(dance_service_module, "QTimer", CapturingTimer)
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )
    service.execute_motion_repeat("Nod", times=3, delay_ms=2000)
    repeat_request_context = dict(service._repeat_request_context)
    service.stop_motion_repeat()

    assert service._busy is True
    assert service._repeat_stop_requested is True
    assert states[-1] == (True, "用户请求停止连续动作: Nod (0/3)")

    service._on_tool_result("execute_motion", {
        "success": True,
        "content": [json.dumps({
            "response": "success",
            "notify": "success",
            "post_action": {
                "exit_motion_engine": "success",
                "set_walk_mode": "success",
            },
        })],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_D04_01_084",
            "profile_key": "oli",
                "request_context": repeat_request_context,
        },
    })

    assert service._busy is False
    assert service._repeat_motion_name == ""
    assert scheduled == []
    assert states[-1] == (False, "连续动作已停止: Nod (1/3)")


def test_busy_service_rejects_and_clears_new_action_authorization(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    service._busy = True

    assert service.authorize_next_action("motion", "Nod", repeat_count=5) is False
    assert service._authorized_action is None


def test_firmware_context_change_preserves_queued_action(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    worker.update_target(
        "HU_D04_01_084",
        OLI_PROFILE.allowed_tools,
        "ws://robot",
        "oli",
    )
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    service.execute_motion_repeat("Nod", times=2)
    pending = next(
        item for item in worker._pending_requests if item[0] == "execute_motion"
    )
    calls = []
    monkeypatch.setattr(
        "workers.mcp_worker.RobotClient.call_tool",
        lambda *_args, **_kwargs: calls.append(True) or {"success": True},
    )

    service.switch_resource_context("oli", "HU_D04_01_084", "v2")
    worker._execute_pending(pending)

    assert calls == [True]
    assert service.resource_context.firmware == "v2"


def test_firmware_update_preserves_sequence_and_rejects_new_sequence(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    scheduled = []

    class CapturingTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((delay_ms, callback))

    monkeypatch.setattr(dance_service_module, "QTimer", CapturingTimer)
    old_sequence = DanceSequence(
        "old",
        [SequenceStep(type="walk", vx=0.1, delay_ms=2000)],
    )
    new_sequence = DanceSequence(
        "new",
        [SequenceStep(type="walk", vx=0.2, delay_ms=3000)],
    )
    service.execute_sequence(old_sequence)
    old_sequence_generation = service._sequence_generation
    service._on_tool_result("set_walk_velocity", {
        "success": True,
        "content": ["{}"],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_D04_01_084",
            "profile_key": "oli",
            "request_context": {
                "sequence_generation": old_sequence_generation,
            },
        },
    })
    old_callback = scheduled[-1][1]
    service.switch_resource_context("oli", "HU_D04_01_084", "v2")
    errors = []
    service.error_occurred.connect(errors.append)
    service.execute_sequence(new_sequence)
    worker._pending_requests.clear()

    assert service._active_sequence is old_sequence
    assert "当前已有舞蹈/动作在执行" in errors[-1]

    old_callback()

    assert service._active_sequence is None
    assert service._busy is False
    assert worker._pending_requests == []


def test_sequence_wait_keeps_busy_and_rejects_manual_action(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    scheduled = []

    class CapturingTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((delay_ms, callback))

    monkeypatch.setattr(dance_service_module, "QTimer", CapturingTimer)
    sequence = DanceSequence(
        "walking",
        [SequenceStep(type="walk", vx=0.1, delay_ms=2000)],
    )
    errors = []
    service.error_occurred.connect(errors.append)

    service.execute_sequence(sequence)
    worker._pending_requests.clear()
    service.execute_motion("manual")

    assert service._busy is True
    assert service._active_sequence is sequence
    assert worker._pending_requests == []
    assert "当前已有舞蹈/动作在执行" in errors[-1]


def test_failed_sequence_walk_step_does_not_advance(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    scheduled = []

    class CapturingTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((delay_ms, callback))

    monkeypatch.setattr(dance_service_module, "QTimer", CapturingTimer)
    sequence = DanceSequence(
        "walking",
        [
            SequenceStep(type="walk", vx=0.1, delay_ms=2000),
            SequenceStep(type="motion", name="Nod"),
        ],
    )
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )
    service.execute_sequence(sequence)
    sequence_generation = service._sequence_generation

    service._on_tool_result("set_walk_velocity", {
        "success": False,
        "content": ["timeout"],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_D04_01_084",
            "profile_key": "oli",
            "request_context": {
                "sequence_generation": sequence_generation,
            },
        },
    })

    assert scheduled == []
    assert service._active_sequence is None
    assert service._busy is False
    assert states[-1] == (False, "序列中止: walking")


def test_action_tool_error_keeps_busy_until_recovery_is_verified(qapp):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    worker.update_target(
        "HU_D04_01_084",
        OLI_PROFILE.allowed_tools,
        "ws://robot",
        "oli",
    )
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )
    service.execute_motion_repeat("Nod", times=3)

    worker._emit_tool_error(
        "execute_motion",
        "timeout",
        worker._target_context(service._repeat_request_context),
    )

    assert service._busy is True
    assert service._action_recovery_pending is True
    assert service._repeat_motion_name == ""
    assert states[-1] == (True, "动作状态未确认，等待机器人空闲")


def test_action_recovery_waits_for_idle_then_restores_walk(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    worker.update_target(
        "HU_D04_01_084",
        OLI_PROFILE.allowed_tools,
        "ws://robot",
        "oli",
    )
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    scheduled = []

    class CapturingTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((delay_ms, callback))

    monkeypatch.setattr(dance_service_module, "QTimer", CapturingTimer)
    states = []
    service.action_state_changed.connect(
        lambda running, label: states.append((running, label))
    )

    service._begin_action_recovery("动作执行状态未确认")
    recovery_generation = service._action_recovery_generation
    assert service._busy is True
    assert scheduled[-1][0] == 0

    scheduled.pop()[1]()
    status_request = worker._pending_requests.pop()
    request_context = status_request[3]
    service._on_tool_result("get_action_library_status", {
        "success": True,
        "content": [json.dumps({
            "result": "success",
            "action_library_mode": "action_library",
            "action_library_state": "running",
        })],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_D04_01_084",
            "profile_key": "oli",
            "request_context": request_context,
        },
    })

    assert service._busy is True
    assert scheduled[-1][0] == 1500
    scheduled.pop()[1]()
    worker._pending_requests.pop()
    service._on_tool_result("get_action_library_status", {
        "success": True,
        "content": [json.dumps({
            "result": "success",
            "action_library_mode": "action_library",
            "action_library_state": "idle",
        })],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_D04_01_084",
            "profile_key": "oli",
            "request_context": request_context,
        },
    })

    recovery_request = worker._pending_requests.pop()
    assert recovery_request[0] == "recover_action_walk"
    service._on_tool_result("recover_action_walk", {
        "success": True,
        "content": [json.dumps({
            "exit_motion_engine": "success",
            "set_walk_mode": "success",
            "verified_mode": "remote_control",
            "verified_state": "idle",
        })],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_D04_01_084",
            "profile_key": "oli",
            "request_context": request_context,
        },
    })

    assert recovery_generation == request_context["generation"]
    assert service._action_recovery_pending is False
    assert service._busy is False
    assert states[-1] == (False, "已确认回到拟人行走模式")


def test_stale_action_error_does_not_stop_new_repeat_batch(qapp):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    worker.update_target(
        "HU_D04_01_084",
        OLI_PROFILE.allowed_tools,
        "ws://robot",
        "oli",
    )
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")
    old_error_context = worker._target_context({
        "kind": "repeat_motion",
        "generation": 1,
    })

    service.switch_resource_context("oli", "HU_D04_01_084", "v2")
    service.execute_motion_repeat("new", times=3)
    current_request_context = dict(service._repeat_request_context)

    worker._emit_tool_error("execute_motion", "old timeout", old_error_context)

    assert service._busy is True
    assert service._repeat_motion_name == "new"
    assert service._repeat_request_context == current_request_context
    assert any(
        item[0] == "execute_motion"
        and item[3] == current_request_context
        for item in worker._pending_requests
    )


@pytest.mark.parametrize("times", [0, 101, True, 2.5, "5"])
def test_repeat_count_must_be_integer_from_one_to_one_hundred(qapp, times):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    service = DanceService(worker)
    errors = []
    service.error_occurred.connect(errors.append)

    service.execute_motion_repeat("Nod", times=times)

    assert worker._pending_requests == []
    assert errors == ["连续动作次数必须为 1 到 100 的整数"]


@pytest.mark.parametrize("times", [1, 100])
def test_repeat_count_accepts_valid_boundaries(qapp, times):
    worker = McpWorker("ws://robot", "HU_D04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("oli", "HU_D04_01_084", "v1")

    service.execute_motion_repeat("Nod", times=times)

    assert worker._pending_requests[-1][:2] == (
        "execute_motion",
        {"motion_name": "Nod"},
    )
    assert service._repeat_motion_total == times


def test_luna_repeat_stops_before_next_action_when_walk_is_lost(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service._repeat_motion_name = "Nod"
    service._repeat_motion_total = 5
    service._repeat_motion_remaining = 4
    service._repeat_motion_done = 1
    service._busy = True
    errors = []
    states = []
    service.error_occurred.connect(errors.append)
    service.action_state_changed.connect(lambda running, label: states.append((running, label)))

    service.update_robot_status({"robot_status": "Damped"})

    service._send_next_repeat_motion()

    assert worker._pending_requests == []
    assert service._busy is False
    assert "仅允许在 Walk 或 Menu 状态执行该操作" in errors[-1]
    assert states[-1] == (False, "连续动作中止: Nod (1/5)")


def test_luna_service_allows_sync_motion_authorization_from_menu(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Menu"})

    assert service.authorize_next_action("motion", "Salute") is True
    service.execute_motion("Salute")

    assert worker._pending_requests[-1][:2] == (
        "execute_motion",
        {"motion_name": "Salute"},
    )


def test_luna_action_authorization_clears_when_status_or_target_changes(qapp):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service.update_robot_status({"robot_status": "Walk"})

    assert service.authorize_next_action("motion", "Nod") is True
    service.update_robot_status({"robot_status": "ZeroTorque"})
    service.update_robot_status({"robot_status": "Walk"})
    service.execute_motion("Nod")
    assert worker._pending_requests == []

    assert service.authorize_next_action("dance", "wakawaka") is True
    service.switch_resource_context("hu_l04_01", "HU_L04_01_085", "v1")
    service.update_robot_status({"robot_status": "Walk"})
    service.execute_dance("wakawaka")
    assert worker._pending_requests == []


def test_action_restore_failure_is_not_counted_or_reported_as_walk(qapp, monkeypatch):
    worker = McpWorker("ws://robot", "HU_L04_01_084")
    worker.update_target(
        "HU_L04_01_084",
        L04_PROFILE.allowed_tools,
        "ws://robot",
        "hu_l04_01",
    )
    service = DanceService(worker)
    service.switch_resource_context("hu_l04_01", "HU_L04_01_084", "v1")
    service._pending_name = "Nod"
    service._busy = True
    increments = []
    errors = []
    labels = []
    monkeypatch.setattr(
        service,
        "_increment_count",
        lambda *_args: increments.append(True),
    )
    service.error_occurred.connect(errors.append)
    service.action_state_changed.connect(lambda _running, label: labels.append(label))

    service._on_tool_result("execute_motion", {
        "success": False,
        "content": [json.dumps({
            "response": "success",
            "notify": "success",
            "post_action": {
                "exit_motion_engine": "success",
                "set_walk_mode": "fail",
            },
        })],
        "_target_context": {
            "generation": worker.target_generation,
            "accid": "HU_L04_01_084",
            "profile_key": "hu_l04_01",
            "request_context": None,
        },
    })

    assert increments == []
    assert any("自动切回拟人行走模式失败" in error for error in errors)
    assert labels[-1] == "动作结束，但未确认恢复拟人行走模式"


def test_connection_workspace_rejects_robot_page_navigation(qtbot, monkeypatch):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    navigated = []
    monkeypatch.setattr(window, "_switch_page", navigated.append)
    window.stack = True

    window._on_navigate("controls")

    assert navigated == []


def test_reapplying_same_workspace_preserves_navigation(qtbot, monkeypatch):
    import config
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window._active_workspace_key = "oli"
    window._active_workspace = OLI_WORKSPACE
    window._connection_service = type(
        "ConnectionServiceStub",
        (),
        {
            "update_ssh": lambda *_: None,
            "update_wifi": lambda *_: None,
        },
    )()
    window._dance_service = type(
        "DanceServiceStub",
        (),
        {
            "switch_resource_context": lambda *_: None,
            "load_dances": lambda *_: None,
            "load_motions": lambda *_: None,
        },
    )()
    window.status_banner = type(
        "StatusBannerStub", (), {"set_identity": lambda *_: None}
    )()
    workspace_updates = []
    navigated = []
    monkeypatch.setattr(window.sidebar, "apply_workspace", workspace_updates.append)
    monkeypatch.setattr(window, "_on_navigate", navigated.append)
    monkeypatch.setattr(window, "_log_ui_event", lambda *args, **kwargs: None)
    identity = RobotIdentity(
        RobotIdentityStatus.READY,
        accid="HU_D04_01_075",
        profile=OLI_PROFILE,
        ssid_accids=("HU_D04_01_075",),
        portal_accid="HU_D04_01_075",
    )

    window.apply_robot_identity(identity)

    assert workspace_updates == []
    assert navigated == []
    assert window._active_workspace is OLI_WORKSPACE
    assert config.ROBOT_CONFIG.ws_accid == "HU_D04_01_075"


def _identity_poll_window():
    from ui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window._active_workspace_key = "oli"
    window._identity_no_target_count = 0
    window._pending_wifi_change_attempt = None
    window._update_wifi_status = lambda _identity: None
    return window


def test_identity_poll_debounces_single_no_target(monkeypatch):
    window = _identity_poll_window()
    no_target = RobotIdentity(
        RobotIdentityStatus.NO_TARGET,
        message="未连接机器人 WiFi",
    )
    locked = []
    applied = []
    monkeypatch.setattr(window, "_lock_transient_identity_loss", locked.append)
    monkeypatch.setattr(window, "apply_robot_identity", applied.append)

    window._on_robot_identity_polled(no_target)

    assert locked == [no_target]
    assert applied == []
    assert window._active_workspace_key == "oli"
    assert window._identity_no_target_count == 1


def test_identity_poll_switches_after_sustained_no_target(monkeypatch):
    window = _identity_poll_window()
    no_target = RobotIdentity(
        RobotIdentityStatus.NO_TARGET,
        message="未连接机器人 WiFi",
    )
    locked = []
    applied = []
    monkeypatch.setattr(window, "_lock_transient_identity_loss", locked.append)
    monkeypatch.setattr(
        window,
        "apply_robot_identity",
        lambda identity: applied.append(identity),
    )

    window._on_robot_identity_polled(no_target)
    window._on_robot_identity_polled(no_target)

    assert locked == [no_target]
    assert applied == [no_target]


def test_identity_poll_restores_same_workspace_after_transient_loss(monkeypatch):
    import config

    window = _identity_poll_window()
    no_target = RobotIdentity(
        RobotIdentityStatus.NO_TARGET,
        message="未连接机器人 WiFi",
    )
    ready = RobotIdentity(
        RobotIdentityStatus.READY,
        accid="HU_D04_01_075",
        profile=OLI_PROFILE,
        ssid_accids=("HU_D04_01_075",),
        portal_accid="HU_D04_01_075",
    )
    locked = []
    applied = []
    monkeypatch.setattr(window, "_lock_transient_identity_loss", locked.append)
    monkeypatch.setattr(
        window,
        "apply_robot_identity",
        lambda identity, message="": applied.append((identity, message)),
    )
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "")
    monkeypatch.setattr(config.ROBOT_CONFIG, "profile_key", "")

    window._on_robot_identity_polled(no_target)
    window._on_robot_identity_polled(ready)

    assert locked == [no_target]
    assert applied == [(ready, "检测到机器人网络变化，已切换控制目标")]
    assert window._active_workspace_key == "oli"
