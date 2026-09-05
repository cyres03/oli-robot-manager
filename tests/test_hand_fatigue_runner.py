import asyncio
import json
import threading

import pytest

from services.hand_fatigue_runner import (
    HAND_FATIGUE_PHASES,
    HandFatigueConfig,
    HandFatigueRunner,
    SET_COMMAND_TITLE,
    SET_RESPONSE_TITLE,
    STATE_COMMAND_TITLE,
    STATE_RESPONSE_TITLE,
    make_hand_command,
)


class FakeConnection:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_args):
        return False


def test_six_phase_payloads_use_expected_modes_and_parameters():
    assert [
        (phase.left_mode, phase.right_mode, phase.profile)
        for phase in HAND_FATIGUE_PHASES
    ] == [
        (1, 1, "uniform"),
        (2, 2, "uniform"),
        (1, 2, "uniform"),
        (2, 1, "uniform"),
        (1, 1, "per_finger"),
        (2, 2, "per_finger"),
    ]

    for phase in HAND_FATIGUE_PHASES:
        command = make_hand_command(
            phase.left_mode,
            phase.right_mode,
            close_hand=True,
            profile=phase.profile,
        )
        assert command["left_mode"] == phase.left_mode
        assert command["right_mode"] == phase.right_mode
        assert len(command["left_pos"]) == 6
        assert len(command["right_pos"]) == 6
        assert ("left_time" in command) is (phase.left_mode == 1)
        assert ("left_vel" in command) is (phase.left_mode == 2)
        assert ("right_time" in command) is (phase.right_mode == 1)
        assert ("right_vel" in command) is (phase.right_mode == 2)


def test_runner_sends_mode_zero_when_control_send_raises():
    class FailingWebSocket:
        def __init__(self):
            self.messages = []
            self.responses = []

        async def send(self, raw):
            message = json.loads(raw)
            self.messages.append(message)
            if message["title"] == STATE_COMMAND_TITLE:
                self.responses.append(json.dumps({
                    "title": STATE_RESPONSE_TITLE,
                    "guid": message["guid"],
                    "data": {"left_pos": [0.0] * 6, "right_pos": [0.0] * 6},
                }))
            elif message["data"] != {"left_mode": 0, "right_mode": 0}:
                raise RuntimeError("simulated control failure")

        async def recv(self):
            return self.responses.pop(0)

    websocket = FailingWebSocket()
    runner = HandFatigueRunner(
        "ws://robot:5000",
        "HU_L04_01_093",
        HandFatigueConfig(duration_seconds=1, cycles_per_phase=1),
        threading.Event(),
        lambda *_args: None,
        connect_factory=lambda *_args, **_kwargs: FakeConnection(websocket),
    )

    with pytest.raises(RuntimeError, match="simulated control failure"):
        asyncio.run(runner.run())

    assert websocket.messages[-1]["title"] == SET_COMMAND_TITLE
    assert websocket.messages[-1]["accid"] == "HU_L04_01_093"
    assert websocket.messages[-1]["data"] == {"left_mode": 0, "right_mode": 0}
    assert runner.stats.stop_sent is True


def test_cancelled_runner_connects_only_to_send_safe_stop():
    class RecordingWebSocket:
        def __init__(self):
            self.messages = []

        async def send(self, raw):
            self.messages.append(json.loads(raw))

    cancel_event = threading.Event()
    cancel_event.set()
    websocket = RecordingWebSocket()
    runner = HandFatigueRunner(
        "ws://robot:5000",
        "HU_D04_01_099",
        HandFatigueConfig(duration_seconds=1, cycles_per_phase=1),
        cancel_event,
        lambda *_args: None,
        connect_factory=lambda *_args, **_kwargs: FakeConnection(websocket),
    )

    stats = asyncio.run(runner.run())

    assert len(websocket.messages) == 1
    assert websocket.messages[0]["data"] == {"left_mode": 0, "right_mode": 0}
    assert stats.stop_reason == "用户取消或机器人目标已切换"
    assert stats.stop_sent is True


def test_normal_protocol_flow_matches_guid_and_sends_safe_stop():
    class ResponsiveWebSocket:
        def __init__(self):
            self.messages = []
            self.responses = []
            self.target = None

        async def send(self, raw):
            message = json.loads(raw)
            self.messages.append(message)
            if message["title"] == SET_COMMAND_TITLE:
                if message["data"] == {"left_mode": 0, "right_mode": 0}:
                    return
                self.target = message["data"]
                self.responses.append(json.dumps({
                    "title": SET_RESPONSE_TITLE,
                    "guid": message["guid"],
                    "data": {"result": "success"},
                }))
            elif message["title"] == STATE_COMMAND_TITLE:
                self.responses.append(json.dumps({
                    "title": STATE_RESPONSE_TITLE,
                    "guid": message["guid"],
                    "data": {
                        "left_pos": (
                            self.target["left_pos"] if self.target else [0.0] * 6
                        ),
                        "right_pos": (
                            self.target["right_pos"] if self.target else [0.0] * 6
                        ),
                    },
                }))

        async def recv(self):
            return self.responses.pop(0)

    class OneActionRunner(HandFatigueRunner):
        async def _run_cycles(self):
            self.stats.loop_rounds = 1
            await self._send_and_monitor(
                make_hand_command(1, 2, close_hand=True),
                close_hand=True,
                phase_index=3,
                phase_name="测试阶段",
                cycle_index=1,
            )
            self.stats.stop_reason = "达到设定测试时长"

    websocket = ResponsiveWebSocket()
    runner = OneActionRunner(
        "ws://robot:5000",
        "HU_D04_01_099",
        HandFatigueConfig(
            duration_seconds=1,
            cycles_per_phase=1,
            state_poll_interval_seconds=0.001,
            open_reached_hold_seconds=0,
        ),
        threading.Event(),
        lambda *_args: None,
        connect_factory=lambda *_args, **_kwargs: FakeConnection(websocket),
    )

    stats = asyncio.run(runner.run())

    assert stats.command_sent == 1
    assert stats.set_ack_success == 1
    assert stats.state_request_sent == 3
    assert stats.failure_count == 0
    assert websocket.messages[-1]["data"] == {"left_mode": 0, "right_mode": 0}


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), 0])
def test_config_rejects_non_finite_or_non_positive_duration(duration):
    with pytest.raises(ValueError, match="时长"):
        HandFatigueConfig(duration_seconds=duration).validate()


def test_blocked_control_send_uses_fallback_connection_for_safe_stop():
    class PrimaryWebSocket:
        def __init__(self):
            self.responses = []

        async def send(self, raw):
            message = json.loads(raw)
            if message["title"] == STATE_COMMAND_TITLE:
                self.responses.append(json.dumps({
                    "title": STATE_RESPONSE_TITLE,
                    "guid": message["guid"],
                    "data": {"left_pos": [0.0] * 6, "right_pos": [0.0] * 6},
                }))
            elif message["data"] == {"left_mode": 0, "right_mode": 0}:
                raise RuntimeError("primary connection unavailable")
            else:
                await asyncio.Event().wait()

        async def recv(self):
            return self.responses.pop(0)

    class BackupWebSocket:
        def __init__(self):
            self.messages = []

        async def send(self, raw):
            self.messages.append(json.loads(raw))

    class OneActionRunner(HandFatigueRunner):
        async def _run_cycles(self):
            await self._send_and_monitor(
                make_hand_command(1, 1, close_hand=True),
                close_hand=True,
                phase_index=1,
                phase_name="发送超时",
                cycle_index=1,
            )

    primary = PrimaryWebSocket()
    backup = BackupWebSocket()
    connections = iter((primary, backup))
    runner = OneActionRunner(
        "ws://robot:5000",
        "HU_L04_01_093",
        HandFatigueConfig(
            duration_seconds=1,
            cycles_per_phase=1,
            send_timeout_seconds=0.01,
        ),
        threading.Event(),
        lambda *_args: None,
        connect_factory=lambda *_args, **_kwargs: FakeConnection(next(connections)),
    )

    with pytest.raises(TimeoutError, match="WebSocket 发送超时"):
        asyncio.run(runner.run())

    assert runner.stats.stop_sent is True
    assert backup.messages[-1]["data"] == {"left_mode": 0, "right_mode": 0}


def test_malformed_preflight_state_blocks_motion_and_still_stops():
    class MalformedStateWebSocket:
        def __init__(self):
            self.messages = []
            self.responses = []

        async def send(self, raw):
            message = json.loads(raw)
            self.messages.append(message)
            if message["title"] == STATE_COMMAND_TITLE:
                self.responses.append(json.dumps({
                    "title": STATE_RESPONSE_TITLE,
                    "guid": message["guid"],
                    "data": {
                        "left_pos": [True, 0, 0, 0, 0, 0],
                        "right_pos": [0.0] * 6,
                    },
                }))

        async def recv(self):
            return self.responses.pop(0)

    websocket = MalformedStateWebSocket()
    runner = HandFatigueRunner(
        "ws://robot:5000",
        "HU_D04_01_099",
        HandFatigueConfig(duration_seconds=1, cycles_per_phase=1),
        threading.Event(),
        lambda *_args: None,
        connect_factory=lambda *_args, **_kwargs: FakeConnection(websocket),
    )

    with pytest.raises(RuntimeError, match="状态预检返回无效"):
        asyncio.run(runner.run())

    set_messages = [
        message for message in websocket.messages
        if message["title"] == SET_COMMAND_TITLE
    ]
    assert [message["data"] for message in set_messages] == [
        {"left_mode": 0, "right_mode": 0},
    ]