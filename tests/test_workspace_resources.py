import json

from models.robot_profile import L04_PROFILE, OLI_PROFILE
from models.workspace import (
    CONNECTION_WORKSPACE,
    LUNA_WORKSPACE,
    OLI_WORKSPACE,
    resolve_workspace,
)
from services.dance_service import DanceService, ResourceContext
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
    assert resolve_workspace(None) is CONNECTION_WORKSPACE
    assert OLI_WORKSPACE.route("calibrate") is not None
    assert LUNA_WORKSPACE.route("calibrate") is None
    assert LUNA_WORKSPACE.route("log_analysis") is not None


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