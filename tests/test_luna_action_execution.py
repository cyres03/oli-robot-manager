import json
import asyncio

import pytest

from network.mcp_client import RobotClient


def _client_with_ready_action_library(monkeypatch, statuses=None):
    client = RobotClient("ws://robot:5000", "HU_L04_01_084")
    calls = []
    status_values = [
        {
            **status,
            "action_library_state": status.get("action_library_state", "idle"),
        }
        for status in (statuses or (
        {"result": "success", "action_library_mode": "remote_control"},
        {"result": "success", "action_library_mode": "action_library"},
        {"result": "success", "action_library_mode": "remote_control"},
        ))
    ]
    status_index = 0

    def get_status():
        nonlocal status_index
        calls.append(("status", None))
        value = status_values[min(status_index, len(status_values) - 1)]
        status_index += 1
        return value

    def set_motion_engine(mode):
        calls.append(("motion_engine", mode))
        return True

    monkeypatch.setattr(client, "get_action_library_status", get_status)
    monkeypatch.setattr(client, "set_motion_engine", set_motion_engine)
    monkeypatch.setattr(
        client,
        "set_walk_mode",
        lambda: calls.append(("walk_mode", None)) or True,
    )
    return client, calls


def test_motion_uses_sync_interface_and_confirms_automatic_walk(monkeypatch):
    client, calls = _client_with_ready_action_library(monkeypatch, [{
        "result": "success",
        "action_library_mode": "remote_control",
        "action_library_state": "idle",
    }])

    def execute_action_sync(names, timeout):
        calls.append(("action_sync", names, timeout))
        return {"result": "success"}

    monkeypatch.setattr(client, "execute_action_sync", execute_action_sync)

    result = client.call_tool("execute_motion", {"motion_name": "Nod"})
    content = json.loads(result["content"][0])

    assert result["success"] is True
    assert content["protocol"] == "request_action_sync"
    assert content["response"] == "success"
    assert content["post_action"] == {
        "exit_motion_engine": "not_needed",
        "set_walk_mode": "not_needed",
        "verified_mode": "remote_control",
        "verified_state": "idle",
        "status": {
            "result": "success",
            "action_library_mode": "remote_control",
            "action_library_state": "idle",
        },
    }
    assert calls == [
        ("action_sync", ["Nod"], 45.0),
        ("status", None),
    ]


def test_action_sync_uses_documented_request_and_timeout(monkeypatch):
    client = RobotClient("ws://robot:5000", "HU_L04_01_084")
    requests = []
    monkeypatch.setattr(
        client,
        "_send_request",
        lambda title, data, timeout: (
            requests.append((title, data, timeout))
            or {"data": {"result": "success"}}
        ),
    )

    result = client.execute_action_sync(["Salute"], timeout=75.0)

    assert result == {"result": "success"}
    assert requests == [
        ("request_action_sync", {"name": "Salute"}, 75.0),
    ]


def test_sync_motion_failure_in_menu_still_restores_and_verifies_walk(
    monkeypatch,
):
    client, calls = _client_with_ready_action_library(monkeypatch)
    exit_statuses = iter((
        {"result": "success", "action_library_mode": "action_library", "action_library_state": "idle"},
        {"result": "success", "action_library_mode": "remote_control", "action_library_state": "idle"},
    ))
    monkeypatch.setattr(
        client,
        "_wait_action_library_quiescent",
        lambda: calls.append(("wait_quiescent", None)) or next(exit_statuses),
    )
    monkeypatch.setattr(
        client,
        "_wait_action_library_exit",
        lambda: calls.append(("wait_exit", None)) or next(exit_statuses),
    )
    monkeypatch.setattr(
        client,
        "execute_action_sync",
        lambda *_args, **_kwargs: {"result": "fail_motor"},
    )

    result = client.call_tool("execute_motion", {"motion_name": "Salute"})
    content = json.loads(result["content"][0])

    assert result["success"] is False
    assert content["response"] == "fail_motor"
    assert content["post_action"]["verified_mode"] == "remote_control"
    assert calls == [
        ("wait_quiescent", None),
        ("motion_engine", 0),
        ("walk_mode", None),
        ("wait_exit", None),
    ]


def test_motion_cleanup_still_attempts_walk_when_engine_exit_times_out(
    monkeypatch,
):
    client, calls = _client_with_ready_action_library(monkeypatch)
    exit_statuses = iter((
        {"result": "success", "action_library_mode": "action_library", "action_library_state": "idle"},
        {"result": "success", "action_library_mode": "remote_control", "action_library_state": "idle"},
    ))
    monkeypatch.setattr(
        client,
        "_wait_action_library_quiescent",
        lambda: calls.append(("wait_quiescent", None)) or next(exit_statuses),
    )
    monkeypatch.setattr(
        client,
        "_wait_action_library_exit",
        lambda: calls.append(("wait_exit", None)) or next(exit_statuses),
    )
    monkeypatch.setattr(
        client,
        "execute_action_sync",
        lambda *_args, **_kwargs: {"result": "success"},
    )

    def set_motion_engine(mode):
        calls.append(("motion_engine", mode))
        if mode == 0:
            raise TimeoutError("exit timeout")
        return True

    monkeypatch.setattr(client, "set_motion_engine", set_motion_engine)

    result = client.call_tool("execute_motion", {"motion_name": "Fist bump"})
    content = json.loads(result["content"][0])

    assert result["success"] is True
    assert content["post_action"]["exit_motion_engine"] == "fail"
    assert content["post_action"]["set_walk_mode"] == "success"
    assert content["post_action"]["verified_mode"] == "remote_control"
    assert calls[-4:] == [
        ("wait_quiescent", None),
        ("motion_engine", 0),
        ("walk_mode", None),
        ("wait_exit", None),
    ]


def test_notify_timeout_after_response_returns_incomplete_result(monkeypatch):
    client = RobotClient("ws://robot:5000", "HU_L04_01_079")

    class FakeWebSocket:
        def __init__(self):
            self.guid = ""
            self.receive_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, payload):
            self.guid = json.loads(payload)["guid"]

        async def recv(self):
            self.receive_count += 1
            if self.receive_count == 1:
                return json.dumps({
                    "title": "response_execute_atomic_motion",
                    "guid": self.guid,
                    "data": {"result": "success"},
                })
            await asyncio.Future()

    websocket = FakeWebSocket()
    monkeypatch.setattr(
        "network.mcp_client.websockets.connect",
        lambda *_args, **_kwargs: websocket,
    )
    audits = []
    monkeypatch.setattr(
        client,
        "_audit",
        lambda event, title, guid, data=None, result=None: audits.append(
            (event, title, result)
        ),
    )

    response, notify = client._send_request_with_notify(
        "request_execute_atomic_motion",
        {"motion_name": "Salute"},
        "notify_execute_atomic_motion",
        timeout=0.01,
    )

    assert response["data"]["result"] == "success"
    assert notify is None
    assert any(event == "notify_timeout" for event, _title, _result in audits)


@pytest.mark.parametrize("event_order", ["notify_before_response", "connection_error"])
def test_stale_notify_or_disconnect_does_not_complete_current_action(
    monkeypatch,
    event_order,
):
    client = RobotClient("ws://robot:5000", "HU_L04_01_079")

    class FakeWebSocket:
        def __init__(self):
            self.guid = ""
            self.receive_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, payload):
            self.guid = json.loads(payload)["guid"]

        async def recv(self):
            self.receive_count += 1
            if event_order == "notify_before_response" and self.receive_count == 1:
                return json.dumps({
                    "title": "notify_execute_atomic_motion",
                    "guid": "stale-request",
                    "data": {"result": "success"},
                })
            response_index = 2 if event_order == "notify_before_response" else 1
            if self.receive_count == response_index:
                return json.dumps({
                    "title": "response_execute_atomic_motion",
                    "guid": self.guid,
                    "data": {"result": "success"},
                })
            if event_order == "connection_error":
                raise ConnectionError("closed")
            await asyncio.Future()

    monkeypatch.setattr(
        "network.mcp_client.websockets.connect",
        lambda *_args, **_kwargs: FakeWebSocket(),
    )

    response, notify = client._send_request_with_notify(
        "request_execute_atomic_motion",
        {"motion_name": "Salute"},
        "notify_execute_atomic_motion",
        timeout=0.01,
    )

    assert response["data"]["result"] == "success"
    assert notify is None


@pytest.mark.parametrize("completion_timeout", [float("inf"), float("nan"), "bad"])
def test_invalid_motion_completion_timeout_uses_default(
    monkeypatch,
    completion_timeout,
):
    client, calls = _client_with_ready_action_library(monkeypatch)

    def execute_motion(names, timeout):
        calls.append(("action_sync", names, timeout))
        return {"result": "success"}

    monkeypatch.setattr(client, "execute_action_sync", execute_motion)

    result = client.call_tool("execute_motion", {
        "motion_name": "Nod",
        "completion_timeout": completion_timeout,
    })

    assert result["success"] is True
    assert ("action_sync", ["Nod"], 45.0) in calls


def test_motion_execution_exception_defers_recovery_while_running(monkeypatch):
    client, calls = _client_with_ready_action_library(monkeypatch)
    monkeypatch.setattr(
        client,
        "_wait_action_library_quiescent",
        lambda: calls.append(("wait_quiescent", None)) or {
            "result": "success",
            "action_library_mode": "action_library",
            "action_library_state": "running",
        },
    )
    monkeypatch.setattr(
        client,
        "execute_action_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("closed")),
    )

    result = client.call_tool("execute_motion", {"motion_name": "Salute"})
    content = json.loads(result["content"][0])

    assert result["success"] is False
    assert content["execution_error"] == "closed"
    assert content["post_action"] == {
        "exit_motion_engine": "deferred",
        "set_walk_mode": "deferred",
        "verified_mode": "action_library",
        "verified_state": "running",
        "status": {
            "result": "success",
            "action_library_mode": "action_library",
            "action_library_state": "running",
        },
        "recovery": "pending",
    }
    assert calls == [("wait_quiescent", None)]


def test_luna_dance_uses_terminal_response_and_restores_walk(monkeypatch):
    client, calls = _client_with_ready_action_library(monkeypatch)

    def send_request(title, data, timeout):
        calls.append(("request", title, data, timeout))
        return {"data": {
            "result": "success",
            "total_actions": 1,
            "total_duration": 23.4,
            "walk_restored": 1,
        }}

    monkeypatch.setattr(client, "_send_request", send_request)
    monkeypatch.setattr(
        client,
        "_send_request_with_notify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Luna dance must not wait for notify_dance")
        ),
    )

    result = client.call_tool("execute_dance", {"dance_name": "wakawaka"})
    content = json.loads(result["content"][0])

    assert result["success"] is True
    assert content["response"] == "success"
    assert content["notify"] is None
    assert content["completion"] == "response"
    assert content["total_actions"] == 1
    assert content["walk_restored"] == 1
    assert content["post_action"] == {
        "exit_motion_engine": "not_needed",
        "set_walk_mode": "not_needed",
        "verified_mode": "remote_control",
        "verified_state": "idle",
        "status": {
            "result": "success",
            "action_library_mode": "remote_control",
            "action_library_state": "idle",
        },
    }
    assert calls == [
        ("status", None),
        ("motion_engine", 1),
        ("status", None),
        ("request", "request_dance", {"name": "wakawaka"}, 240.0),
        ("status", None),
    ]


def test_luna_dance_rejects_non_terminal_success_response(monkeypatch):
    client, _calls = _client_with_ready_action_library(monkeypatch)
    monkeypatch.setattr(
        client,
        "_send_request",
        lambda *_args, **_kwargs: {"data": {"result": "success"}},
    )

    result = client.call_tool("execute_dance", {"dance_name": "wakawaka"})
    content = json.loads(result["content"][0])

    assert result["success"] is False
    assert content["completion"] == "incomplete_response"


@pytest.mark.parametrize("field,value", [
    ("total_actions", 0),
    ("total_actions", True),
    ("total_duration", 0),
    ("total_duration", "23.4"),
    ("walk_restored", 0),
    ("walk_restored", True),
])
def test_luna_dance_rejects_invalid_terminal_values(monkeypatch, field, value):
    client, _calls = _client_with_ready_action_library(monkeypatch)
    response = {
        "result": "success",
        "total_actions": 1,
        "total_duration": 23.4,
        "walk_restored": 1,
    }
    response[field] = value
    monkeypatch.setattr(
        client,
        "_send_request",
        lambda *_args, **_kwargs: {"data": response},
    )

    result = client.call_tool("execute_dance", {"dance_name": "wakawaka"})

    assert result["success"] is False


def test_action_restore_failure_marks_whole_operation_failed(monkeypatch):
    client, calls = _client_with_ready_action_library(monkeypatch, [
        {"result": "success", "action_library_mode": "action_library"},
    ])
    monkeypatch.setattr(
        client,
        "execute_action_sync",
        lambda *_args, **_kwargs: {"result": "success"},
    )
    monkeypatch.setattr(
        client,
        "set_walk_mode",
        lambda: calls.append(("walk_mode_failed", None)) or False,
    )

    result = client.call_tool("execute_motion", {"motion_name": "Nod"})
    content = json.loads(result["content"][0])

    assert result["success"] is False
    assert content["post_action"]["set_walk_mode"] == "fail"
    assert content["post_action"]["verified_mode"] == "action_library"


def test_remote_control_running_is_not_treated_as_recovered(monkeypatch):
    client, calls = _client_with_ready_action_library(monkeypatch)
    monkeypatch.setattr(
        client,
        "_wait_action_library_quiescent",
        lambda: calls.append(("wait_quiescent", None)) or {
            "result": "success",
            "action_library_mode": "remote_control",
            "action_library_state": "running",
        },
    )
    monkeypatch.setattr(
        client,
        "execute_action_sync",
        lambda *_args, **_kwargs: {"result": "success"},
    )

    result = client.call_tool("execute_motion", {"motion_name": "Nod"})
    content = json.loads(result["content"][0])

    assert result["success"] is False
    assert content["post_action"]["verified_state"] == "running"
    assert content["post_action"]["recovery"] == "pending"
    assert calls == [("wait_quiescent", None)]


def test_oli_dance_still_requires_completion_notification(monkeypatch):
    client = RobotClient("ws://robot:5000", "HU_D04_01_084")
    monkeypatch.setattr(
        client,
        "_send_request_with_notify",
        lambda *_args, **_kwargs: (
            {"data": {"result": "success"}},
            {"data": {"result": "success"}},
        ),
    )

    result = client.execute_dance("whatever", timeout=10)

    assert result == {"response": "success", "notify": "success"}


def test_sync_motion_failure_does_not_fallback_to_atomic_motion(
    monkeypatch,
):
    client, _calls = _client_with_ready_action_library(monkeypatch, [{
        "result": "success",
        "action_library_mode": "remote_control",
    }])
    action_calls = []
    monkeypatch.setattr(
        client,
        "execute_action_sync",
        lambda *_args, **_kwargs: {
            "result": "fail_state_not_allowed",
            "current_state": "Damped",
        },
    )
    monkeypatch.setattr(client, "execute_atomic_motion", lambda *_args: action_calls.append(True))

    result = client.call_tool("execute_motion", {"motion_name": "Nod"})

    assert result["success"] is False
    assert action_calls == []