import config
from config import RobotConfig
from models.robot_profile import L04_PROFILE, TRON2_PROFILE, RobotIdentityStatus
from network.wifi_manager import WifiManager
from workers.mcp_worker import McpWorker


def test_detect_identity_cross_checks_connected_l04(monkeypatch):
    monkeypatch.setattr(
        WifiManager,
        "get_connected_robot_ssids",
        staticmethod(lambda: ["HU_L04_01_091_5G"]),
    )
    monkeypatch.setattr(
        config,
        "detect_accid_from_robot_portal",
        lambda timeout=2.0: "HU_L04_01_091",
    )

    identity = config.detect_robot_identity()

    assert identity.status == RobotIdentityStatus.READY
    assert identity.profile is L04_PROFILE


def test_no_connected_robot_does_not_probe_portal(monkeypatch):
    portal_calls = []
    monkeypatch.setattr(
        WifiManager,
        "get_connected_robot_ssids",
        staticmethod(lambda: []),
    )
    monkeypatch.setattr(
        config,
        "detect_accid_from_robot_portal",
        lambda timeout=2.0: portal_calls.append(timeout),
    )

    identity = config.detect_robot_identity()

    assert identity.status == RobotIdentityStatus.NO_TARGET
    assert portal_calls == []


def test_applying_l04_profile_updates_legacy_config_fields():
    identity = config.resolve_robot_identity(
        ["HU_L04_01_091_5G"],
        "HU_L04_01_091",
    )
    robot_config = RobotConfig()

    assert robot_config.apply_identity(identity) is True
    assert robot_config.ws_accid == "HU_L04_01_091"
    assert robot_config.profile_key == "hu_l04_01"
    assert robot_config.main_control_ip == "10.192.1.2"
    assert robot_config.perception_ip == "10.192.1.4"
    assert robot_config.expected_cpu_cores == 8
    assert robot_config.expected_motor_count == 27
    assert robot_config.mcp_supported is False
    assert robot_config.mcp_url == ""
    assert robot_config.allow_cpu_repair is False
    assert robot_config.allow_time_repair is False


def test_worker_blocks_l04_control_and_drops_stale_queue(qapp):
    worker = McpWorker(
        "ws://10.192.1.2:5000",
        "HU_L04_01_091",
        allowed_tools=L04_PROFILE.allowed_tools,
    )
    errors = []
    worker.tool_error.connect(lambda name, detail: errors.append((name, detail)))

    worker.call_tool("execute_motion", {"motion_name": "wave"})
    worker.call_tool("get_motions", {})

    assert errors == [("execute_motion", "当前机器人型号未开放此能力")]
    assert worker._pending_requests[0][:2] == ("get_motions", {})

    worker.update_target("HU_L04_01_092", L04_PROFILE.allowed_tools)

    assert worker._pending_requests == []


def test_applying_tron2_profile_updates_documented_endpoints():
    identity = config.resolve_robot_identity(
        ["WF_TRON2A_185"],
        "WF_TRON2A_185",
    )
    robot_config = RobotConfig()

    assert robot_config.apply_identity(identity) is True
    assert robot_config.ws_accid == "WF_TRON2A_185"
    assert robot_config.profile_key == "tron2"
    assert robot_config.main_control_ip == "10.192.1.2"
    assert robot_config.perception_ip == "10.192.1.4"
    assert robot_config.perception_user == "guest"
    assert robot_config.portal_url == "http://10.192.1.2:8080"
    assert robot_config.logs_url == ""
    assert robot_config.websocket_url == "ws://10.192.1.2:5000"
    assert robot_config.mcp_supported is False
    assert robot_config.mcp_url == ""
    assert robot_config.expected_motor_count is None


def test_worker_blocks_all_tron2_tools(qapp):
    worker = McpWorker(
        "ws://10.192.1.2:5000",
        "WF_TRON2A_185",
        allowed_tools=TRON2_PROFILE.allowed_tools,
    )
    errors = []
    worker.tool_error.connect(lambda name, detail: errors.append((name, detail)))

    worker.call_tool("get_motions", {})
    worker.call_tool("set_walk_velocity", {"x": 0.1, "y": 0, "yaw": 0})

    assert errors == [
        ("get_motions", "当前机器人型号未开放此能力"),
        ("set_walk_velocity", "当前机器人型号未开放此能力"),
    ]
    assert worker._pending_requests == []