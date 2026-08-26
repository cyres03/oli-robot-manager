import json

from models.robot_profile import (
    L04_PROFILE,
    OLI_PROFILE,
    RobotIdentity,
    RobotIdentityStatus,
)
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
    assert OLI_WORKSPACE.route("test_cases") is None
    assert LUNA_WORKSPACE.route("calibrate") is None
    assert LUNA_WORKSPACE.route("log_analysis") is not None
    assert LUNA_WORKSPACE.route("test_cases") is not None


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
    assert service._busy is False


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
        "ConnectionServiceStub", (), {"update_ssh": lambda *_: None}
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
    return window


def test_identity_poll_debounces_single_no_target(monkeypatch):
    import config

    window = _identity_poll_window()
    no_target = RobotIdentity(
        RobotIdentityStatus.NO_TARGET,
        message="未连接机器人 WiFi",
    )
    locked = []
    applied = []
    monkeypatch.setattr(config, "detect_robot_identity", lambda timeout: no_target)
    monkeypatch.setattr(window, "_lock_transient_identity_loss", locked.append)
    monkeypatch.setattr(window, "apply_robot_identity", applied.append)

    window._poll_robot_identity()

    assert locked == [no_target]
    assert applied == []
    assert window._active_workspace_key == "oli"
    assert window._identity_no_target_count == 1


def test_identity_poll_switches_after_sustained_no_target(monkeypatch):
    import config

    window = _identity_poll_window()
    no_target = RobotIdentity(
        RobotIdentityStatus.NO_TARGET,
        message="未连接机器人 WiFi",
    )
    locked = []
    applied = []
    monkeypatch.setattr(config, "detect_robot_identity", lambda timeout: no_target)
    monkeypatch.setattr(window, "_lock_transient_identity_loss", locked.append)
    monkeypatch.setattr(
        window,
        "apply_robot_identity",
        lambda identity: applied.append(identity),
    )

    window._poll_robot_identity()
    window._poll_robot_identity()

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
    identities = iter((no_target, ready))
    locked = []
    applied = []
    monkeypatch.setattr(
        config,
        "detect_robot_identity",
        lambda timeout: next(identities),
    )
    monkeypatch.setattr(window, "_lock_transient_identity_loss", locked.append)
    monkeypatch.setattr(
        window,
        "apply_robot_identity",
        lambda identity, message="": applied.append((identity, message)),
    )
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "")
    monkeypatch.setattr(config.ROBOT_CONFIG, "profile_key", "")

    window._poll_robot_identity()
    window._poll_robot_identity()

    assert locked == [no_target]
    assert applied == [(ready, "检测到机器人网络变化，已切换控制目标")]
    assert window._active_workspace_key == "oli"
