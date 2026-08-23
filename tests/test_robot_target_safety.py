import pytest

import config
import network.mcp_client as mcp_client_module
from network.mcp_client import RobotClient
from network.wifi_manager import WifiManager
from workers.mcp_worker import McpWorker


def test_detection_returns_no_target_instead_of_default_oli(monkeypatch):
    monkeypatch.setattr(config, "detect_accid_from_robot_portal", lambda: None)
    monkeypatch.setattr(WifiManager, "get_robot_ssid", staticmethod(lambda: None))

    assert config.detect_accid_from_wifi() is None


def test_robot_client_rejects_unresolved_target_before_connect(monkeypatch):
    connect_calls = []
    monkeypatch.setattr(
        mcp_client_module.websockets,
        "connect",
        lambda *args, **kwargs: connect_calls.append((args, kwargs)),
    )
    client = RobotClient("ws://10.192.1.2:5000", None)

    with pytest.raises(RuntimeError, match="未识别机器人"):
        client._send_request("request_get_joint_state")

    with pytest.raises(RuntimeError, match="未识别机器人"):
        client._send_command("request_set_walk_vel")

    with pytest.raises(RuntimeError, match="未识别机器人"):
        client._send_request_with_notify(
            "request_calibrate",
            {},
            "notify_calibrate",
        )

    assert connect_calls == []


def test_worker_rejects_unresolved_target_and_recovers(qapp):
    worker = McpWorker("ws://10.192.1.2:5000", None)
    errors = []
    connection_states = []
    worker.tool_error.connect(lambda name, detail: errors.append((name, detail)))
    worker.mcp_connected.connect(connection_states.append)

    worker.call_tool("get_motions", {})

    assert worker._pending_requests == []
    assert errors == [("get_motions", "未识别机器人，命令未发送")]
    assert connection_states == [False]

    worker.update_accid("HU_L04_01_091")
    worker.call_tool("get_motions", {})

    assert connection_states[-1] is True
    assert worker._pending_requests == [("get_motions", {})]