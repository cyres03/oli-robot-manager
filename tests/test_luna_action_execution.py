import json

import pytest

from network.mcp_client import RobotClient


def _client_with_ready_action_library(monkeypatch):
    client = RobotClient("ws://robot:5000", "HU_L04_01_084")
    calls = []
    statuses = iter((
        {"result": "success", "action_library_mode": "remote_control"},
        {"result": "success", "action_library_mode": "action_library"},
    ))

    def get_status():
        calls.append(("status", None))
        return next(statuses)

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


def test_luna_atomic_motion_enters_library_waits_notify_and_restores_walk(
    monkeypatch,
):
    client, calls = _client_with_ready_action_library(monkeypatch)

    def execute_motion(name, timeout):
        calls.append(("execute_motion", name, timeout))
        return {"response": "success", "notify": "success"}

    monkeypatch.setattr(client, "execute_atomic_motion", execute_motion)

    result = client.call_tool("execute_motion", {"motion_name": "Nod"})
    content = json.loads(result["content"][0])

    assert result["success"] is True
    assert content["response"] == "success"
    assert content["notify"] == "success"
    assert content["post_action"] == {
        "exit_motion_engine": "success",
        "set_walk_mode": "success",
    }
    assert calls == [
        ("status", None),
        ("motion_engine", 1),
        ("status", None),
        ("execute_motion", "Nod", 45.0),
        ("motion_engine", 0),
        ("walk_mode", None),
    ]


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
        "exit_motion_engine": "success",
        "set_walk_mode": "success",
    }
    assert calls == [
        ("status", None),
        ("motion_engine", 1),
        ("status", None),
        ("request", "request_dance", {"name": "wakawaka"}, 240.0),
        ("motion_engine", 0),
        ("walk_mode", None),
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
    client, calls = _client_with_ready_action_library(monkeypatch)
    monkeypatch.setattr(
        client,
        "execute_atomic_motion",
        lambda *_args, **_kwargs: {"response": "success", "notify": "success"},
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


def test_action_is_not_sent_when_action_library_does_not_become_ready(
    monkeypatch,
):
    client = RobotClient("ws://robot:5000", "HU_L04_01_084")
    action_calls = []
    monkeypatch.setattr(
        client,
        "get_action_library_status",
        lambda: {"result": "success", "action_library_mode": "remote_control"},
    )
    monkeypatch.setattr(client, "set_motion_engine", lambda _mode: False)
    monkeypatch.setattr(
        client,
        "execute_atomic_motion",
        lambda *_args, **_kwargs: action_calls.append(True),
    )

    result = client.call_tool("execute_motion", {"motion_name": "Nod"})

    assert result["success"] is False
    assert action_calls == []