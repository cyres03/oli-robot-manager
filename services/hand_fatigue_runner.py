"""BrainCo dual-hand fatigue protocol runner used by managed tests."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import json
import math
import threading
import time
from typing import Any
import uuid

import websockets


SET_COMMAND_TITLE = "request_set_brainco2_hand_cmd"
SET_RESPONSE_TITLE = "response_set_brainco2_hand_cmd"
STATE_COMMAND_TITLE = "request_get_brainco2_hand_state"
STATE_RESPONSE_TITLE = "response_get_brainco2_hand_state"

POSITION_MAX = (1.0297, 1.5707, 1.4137, 1.4137, 1.4137, 1.4137)
VELOCITY_MAX = (2.5367, 2.6180, 2.2689, 2.2689, 2.2689, 2.2689)
POSITION_TOLERANCE = 0.05
SAFE_CLOSE_POSITION = (
    0.6,
    0.6,
    POSITION_MAX[2] * 0.9,
    POSITION_MAX[3] * 0.9,
    POSITION_MAX[4] * 0.9,
    POSITION_MAX[5] * 0.9,
)
UNIFORM_CLOSE_TIME_MS = (1,) * 6
UNIFORM_OPEN_TIME_MS = (2000,) * 6
UNIFORM_CLOSE_VELOCITY = (0.1,) * 6
UNIFORM_OPEN_VELOCITY = VELOCITY_MAX
FINGER_CLOSE_TIME_MS = (1, 400, 800, 1200, 1600, 2000)
FINGER_OPEN_TIME_MS = (2000, 1600, 1200, 800, 400, 1)
FINGER_CLOSE_VELOCITY = (0.1, 0.2, 0.6, 1.0, 1.6, 2.2)
FINGER_OPEN_VELOCITY = (2.5367, 2.6180, 2.2689, 1.6, 0.8, 0.2)


@dataclass(frozen=True)
class HandFatiguePhase:
    name: str
    left_mode: int
    right_mode: int
    profile: str


HAND_FATIGUE_PHASES = (
    HandFatiguePhase("阶段1 同构控制: 左1 右1（统一参数）", 1, 1, "uniform"),
    HandFatiguePhase("阶段2 同构控制: 左2 右2（统一参数）", 2, 2, "uniform"),
    HandFatiguePhase("阶段3 异构控制: 左1 右2（统一参数）", 1, 2, "uniform"),
    HandFatiguePhase("阶段4 异构控制: 左2 右1（统一参数）", 2, 1, "uniform"),
    HandFatiguePhase("阶段5 同构控制: 左1 右1（每指不同时间）", 1, 1, "per_finger"),
    HandFatiguePhase("阶段6 同构控制: 左2 右2（每指不同速度）", 2, 2, "per_finger"),
)


@dataclass(frozen=True)
class HandFatigueConfig:
    duration_seconds: float = 7200.0
    cycles_per_phase: int = 10
    state_timeout_seconds: float = 2.0
    state_poll_interval_seconds: float = 0.05
    arrival_timeout_seconds: float = 8.0
    required_consecutive_reached: int = 2
    open_reached_hold_seconds: float = 0.15
    send_timeout_seconds: float = 2.0

    def validate(self):
        if (
            not math.isfinite(self.duration_seconds)
            or not 1 <= self.duration_seconds <= 86400
        ):
            raise ValueError("疲劳测试时长必须在 1 秒到 24 小时之间")
        if not 1 <= self.cycles_per_phase <= 100:
            raise ValueError("每阶段循环次数必须在 1 到 100 之间")
        if self.state_timeout_seconds <= 0 or self.arrival_timeout_seconds <= 0:
            raise ValueError("状态和到位超时必须大于 0")
        if self.state_poll_interval_seconds <= 0:
            raise ValueError("状态轮询间隔必须大于 0")
        if self.required_consecutive_reached < 1:
            raise ValueError("连续到位帧数必须大于 0")
        timeout_values = (
            self.state_timeout_seconds,
            self.state_poll_interval_seconds,
            self.arrival_timeout_seconds,
            self.open_reached_hold_seconds,
            self.send_timeout_seconds,
        )
        if not all(math.isfinite(value) for value in timeout_values):
            raise ValueError("疲劳测试超时参数必须是有限数值")
        if self.open_reached_hold_seconds < 0 or self.send_timeout_seconds <= 0:
            raise ValueError("停留时间不能为负且发送超时必须大于 0")


@dataclass
class HandFatigueStats:
    started_monotonic: float = field(default_factory=time.monotonic)
    completed_monotonic: float = 0.0
    command_sent: int = 0
    set_ack_success: int = 0
    set_ack_fail_motor: int = 0
    set_ack_fail_invalid_cmd: int = 0
    set_ack_other_fail: int = 0
    set_ack_timeout: int = 0
    state_request_sent: int = 0
    state_timeout: int = 0
    arrival_timeout_count: int = 0
    position_mismatch_count: int = 0
    position_max_error_rad: float = 0.0
    position_out_of_range_count: int = 0
    malformed_state_count: int = 0
    loop_rounds: int = 0
    stop_reason: str = ""
    stop_sent: bool = False
    stop_error: str = ""
    anomaly_samples: list[str] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_monotonic or time.monotonic()
        return max(0.0, end - self.started_monotonic)

    @property
    def failure_count(self) -> int:
        return sum((
            self.set_ack_fail_motor,
            self.set_ack_fail_invalid_cmd,
            self.set_ack_other_fail,
            self.set_ack_timeout,
            self.state_timeout,
            self.arrival_timeout_count,
            self.position_mismatch_count,
            self.position_out_of_range_count,
            self.malformed_state_count,
            int(not self.stop_sent),
        ))

    def add_anomaly(self, text: str, limit: int = 100):
        if len(self.anomaly_samples) < limit:
            self.anomaly_samples.append(text)


def build_hand_message(accid: str, title: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "accid": accid,
        "title": title,
        "timestamp": int(time.time() * 1000),
        "guid": uuid.uuid4().hex,
        "data": data,
    }


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def make_hand_command(
    left_mode: int,
    right_mode: int,
    close_hand: bool,
    profile: str = "uniform",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "left_mode": left_mode,
        "right_mode": right_mode,
    }
    target_position = list(SAFE_CLOSE_POSITION if close_hand else (0.0,) * 6)
    if profile == "per_finger":
        hand_time = FINGER_CLOSE_TIME_MS if close_hand else FINGER_OPEN_TIME_MS
        hand_velocity = (
            FINGER_CLOSE_VELOCITY if close_hand else FINGER_OPEN_VELOCITY
        )
    else:
        hand_time = UNIFORM_CLOSE_TIME_MS if close_hand else UNIFORM_OPEN_TIME_MS
        hand_velocity = (
            UNIFORM_CLOSE_VELOCITY if close_hand else UNIFORM_OPEN_VELOCITY
        )

    for side, mode in (("left", left_mode), ("right", right_mode)):
        data[f"{side}_pos"] = target_position.copy()
        if mode == 1:
            data[f"{side}_time"] = list(hand_time)
        elif mode == 2:
            data[f"{side}_vel"] = list(hand_velocity)
        else:
            raise ValueError(f"疲劳测试不支持 {side}_mode={mode}")
    return data


class HandFatigueRunner:
    def __init__(
        self,
        ws_url: str,
        accid: str,
        config: HandFatigueConfig,
        cancel_event: threading.Event,
        output: Callable[[str, str], None],
        connect_factory=None,
    ):
        config.validate()
        if not ws_url:
            raise ValueError("机器人 WebSocket 地址为空")
        if not accid:
            raise ValueError("机器人 ACCID 为空")
        self.ws_url = ws_url
        self.accid = accid
        self.config = config
        self.cancel_event = cancel_event
        self.output = output
        self.connect_factory = connect_factory or websockets.connect
        self.stats = HandFatigueStats()
        self._websocket = None

    async def run(self) -> HandFatigueStats:
        try:
            async with self.connect_factory(
                self.ws_url,
                max_size=None,
                ping_interval=20,
                ping_timeout=10,
                open_timeout=10,
                close_timeout=2,
            ) as websocket:
                self._websocket = websocket
                self._emit(f"[连接成功] {self.ws_url}, accid={self.accid}")
                try:
                    if self.cancel_event.is_set():
                        self.stats.stop_reason = "用户取消或机器人目标已切换"
                    else:
                        await self._preflight()
                    if not self.cancel_event.is_set():
                        await self._run_cycles()
                finally:
                    await self._send_stop()
        finally:
            self.stats.completed_monotonic = time.monotonic()
            self._websocket = None
        return self.stats

    async def _preflight(self):
        request = build_hand_message(self.accid, STATE_COMMAND_TITLE, {})
        await self._send_message(request)
        self.stats.state_request_sent += 1
        response = await self._receive_until(
            STATE_RESPONSE_TITLE,
            request["guid"],
            self.config.state_timeout_seconds,
        )
        if self.cancel_event.is_set():
            return
        if response is None:
            self.stats.state_timeout += 1
            raise RuntimeError("灵巧手状态预检超时，已阻止疲劳测试")
        state = response.get("data")
        if not isinstance(state, dict) or not self._has_valid_hand_positions(state):
            self.stats.malformed_state_count += 1
            raise RuntimeError("灵巧手状态预检返回无效，已阻止疲劳测试")
        violations = self._position_range_violations(state)
        if violations:
            self.stats.position_out_of_range_count += 1
            raise RuntimeError(
                f"灵巧手状态预检发现位置越界: {sorted(violations)}"
            )
        self._emit("[预检通过] 已读取左右灵巧手状态")

    async def _run_cycles(self):
        end_at = self.stats.started_monotonic + self.config.duration_seconds
        while not self.cancel_event.is_set() and time.monotonic() < end_at:
            self.stats.loop_rounds += 1
            self._emit(f"--- 测试轮次 {self.stats.loop_rounds} ---")
            for phase_index, phase in enumerate(HAND_FATIGUE_PHASES, start=1):
                if self.cancel_event.is_set() or time.monotonic() >= end_at:
                    break
                self._emit(f"[{phase.name}]")
                for cycle_index in range(1, self.config.cycles_per_phase + 1):
                    if self.cancel_event.is_set() or time.monotonic() >= end_at:
                        break
                    for close_hand in (True, False):
                        if self.cancel_event.is_set() or time.monotonic() >= end_at:
                            break
                        command = make_hand_command(
                            phase.left_mode,
                            phase.right_mode,
                            close_hand,
                            phase.profile,
                        )
                        await self._send_and_monitor(
                            command,
                            close_hand,
                            phase_index,
                            phase.name,
                            cycle_index,
                        )

        self.stats.stop_reason = (
            "用户取消或机器人目标已切换"
            if self.cancel_event.is_set()
            else "达到设定测试时长"
        )

    async def _send_and_monitor(
        self,
        hand_data: dict[str, Any],
        close_hand: bool,
        phase_index: int,
        phase_name: str,
        cycle_index: int,
    ):
        message = build_hand_message(self.accid, SET_COMMAND_TITLE, hand_data)
        await self._send_message(message)
        self.stats.command_sent += 1
        action = "闭合" if close_hand else "张开"
        context = (
            f"轮次{self.stats.loop_rounds} 阶段{phase_index}({phase_name}) "
            f"循环{cycle_index} 动作{action}"
        )

        acknowledgement = await self._receive_until(
            SET_RESPONSE_TITLE,
            message["guid"],
            self.config.state_timeout_seconds,
        )
        if self.cancel_event.is_set():
            return
        if acknowledgement is None:
            self.stats.set_ack_timeout += 1
            self.stats.add_anomaly(f"{context} set ACK 超时")
        else:
            data = acknowledgement.get("data")
            result = data.get("result", "unknown") if isinstance(data, dict) else "unknown"
            if result == "success":
                self.stats.set_ack_success += 1
            elif result == "fail_motor":
                self.stats.set_ack_fail_motor += 1
                self.stats.add_anomaly(f"{context} set ACK: fail_motor")
            elif result == "fail_invalid_cmd":
                self.stats.set_ack_fail_invalid_cmd += 1
                self.stats.add_anomaly(f"{context} set ACK: fail_invalid_cmd")
            else:
                self.stats.set_ack_other_fail += 1
                self.stats.add_anomaly(f"{context} set ACK: {result}")

        deadline = time.monotonic() + self.config.arrival_timeout_seconds
        consecutive_reached = 0
        received_state = False
        latest_state: dict[str, Any] = {}
        out_of_range: set[str] = set()
        while not self.cancel_event.is_set() and time.monotonic() < deadline:
            request = build_hand_message(self.accid, STATE_COMMAND_TITLE, {})
            await self._send_message(request)
            self.stats.state_request_sent += 1
            response = await self._receive_until(
                STATE_RESPONSE_TITLE,
                request["guid"],
                self.config.state_timeout_seconds,
            )
            if self.cancel_event.is_set():
                return
            if response is None:
                self.stats.state_timeout += 1
                self.stats.add_anomaly(f"{context} state 查询超时")
                await self._interruptible_sleep(
                    self.config.state_poll_interval_seconds
                )
                continue
            state = response.get("data")
            if not isinstance(state, dict):
                self.stats.malformed_state_count += 1
                self.stats.add_anomaly(f"{context} state 数据格式无效")
                continue
            received_state = True
            latest_state = state
            out_of_range.update(self._position_range_violations(state))
            max_error = self._position_max_error(hand_data, state)
            self.stats.position_max_error_rad = max(
                self.stats.position_max_error_rad,
                max_error,
            )
            if self._is_position_reached(hand_data, state):
                consecutive_reached += 1
                if consecutive_reached >= self.config.required_consecutive_reached:
                    if not close_hand:
                        await self._interruptible_sleep(
                            self.config.open_reached_hold_seconds
                        )
                    break
            else:
                consecutive_reached = 0
            await self._interruptible_sleep(
                self.config.state_poll_interval_seconds
            )
        else:
            if not self.cancel_event.is_set():
                self.stats.arrival_timeout_count += 1
                if received_state:
                    max_error = self._position_max_error(hand_data, latest_state)
                    if max_error > POSITION_TOLERANCE:
                        self.stats.position_mismatch_count += 1
                    self.stats.add_anomaly(
                        f"{context} 到位超时，最大偏差={max_error:.4f}rad"
                    )
                else:
                    self.stats.add_anomaly(f"{context} 到位超时，未收到有效 state")

        if out_of_range:
            self.stats.position_out_of_range_count += 1
            self.stats.add_anomaly(
                f"{context} pos 越界，手指={sorted(out_of_range)}"
            )
        self._emit(f"{context}: {'到位' if consecutive_reached else '未到位'}")

    async def _receive_until(
        self,
        expected_title: str,
        guid: str,
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_seconds
        while not self.cancel_event.is_set() and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                raw = await asyncio.wait_for(
                    self._websocket.recv(),
                    timeout=min(0.2, max(0.01, remaining)),
                )
            except asyncio.TimeoutError:
                continue
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                self.stats.malformed_state_count += 1
                continue
            if (
                isinstance(message, dict)
                and message.get("title") == expected_title
                and message.get("guid") == guid
            ):
                return message
        return None

    async def _send_stop(self):
        if self._websocket is None:
            return
        message = build_hand_message(
            self.accid,
            SET_COMMAND_TITLE,
            {"left_mode": 0, "right_mode": 0},
        )
        payload = json.dumps(message, ensure_ascii=False)
        primary_error = None
        try:
            await asyncio.wait_for(
                self._websocket.send(payload),
                timeout=self.config.send_timeout_seconds,
            )
            self.stats.stop_sent = True
            self._emit("[安全停止] 已向双手发送 mode=0")
        except Exception as error:
            primary_error = error
            self._emit(
                f"[安全停止重试] 主连接发送失败: {error}",
                "stderr",
            )

        if self.stats.stop_sent:
            return
        try:
            async with self.connect_factory(
                self.ws_url,
                max_size=None,
                ping_interval=20,
                ping_timeout=10,
                open_timeout=3,
                close_timeout=2,
            ) as websocket:
                await asyncio.wait_for(
                    websocket.send(payload),
                    timeout=self.config.send_timeout_seconds,
                )
            self.stats.stop_sent = True
            self._emit("[安全停止] 已通过备用连接发送双手 mode=0")
        except Exception as retry_error:
            errors = [str(error) for error in (primary_error, retry_error) if error]
            self.stats.stop_error = "; ".join(errors) or "未知错误"
            self.stats.add_anomaly(f"双手 mode=0 发送失败: {self.stats.stop_error}")
            self._emit(f"[安全停止失败] {self.stats.stop_error}", "stderr")

    async def _send_message(self, message: dict[str, Any]):
        try:
            await asyncio.wait_for(
                self._websocket.send(json.dumps(message, ensure_ascii=False)),
                timeout=self.config.send_timeout_seconds,
            )
        except asyncio.TimeoutError as error:
            raise TimeoutError(
                f"WebSocket 发送超时 ({self.config.send_timeout_seconds:g} 秒)"
            ) from error

    async def _interruptible_sleep(self, delay: float):
        deadline = time.monotonic() + max(0.0, delay)
        while not self.cancel_event.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(min(0.1, deadline - time.monotonic()))

    def _position_range_violations(self, state: dict[str, Any]) -> set[str]:
        violations = set()
        for side in ("left", "right"):
            positions = state.get(f"{side}_pos")
            if not isinstance(positions, list) or len(positions) != 6:
                self.stats.malformed_state_count += 1
                continue
            for index, value in enumerate(positions):
                if not _is_finite_number(value):
                    self.stats.malformed_state_count += 1
                elif not 0 <= value <= POSITION_MAX[index]:
                    violations.add(f"{side}[{index}]")
        return violations

    @staticmethod
    def _has_valid_hand_positions(state: dict[str, Any]) -> bool:
        for side in ("left", "right"):
            positions = state.get(f"{side}_pos")
            if not isinstance(positions, list) or len(positions) != 6:
                return False
            if not all(_is_finite_number(value) for value in positions):
                return False
        return True

    @staticmethod
    def _position_max_error(
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> float:
        max_error = 0.0
        for side in ("left", "right"):
            expected_positions = expected.get(f"{side}_pos")
            actual_positions = actual.get(f"{side}_pos")
            if not (
                isinstance(expected_positions, list)
                and isinstance(actual_positions, list)
                and len(expected_positions) == 6
                and len(actual_positions) == 6
            ):
                continue
            for expected_value, actual_value in zip(
                expected_positions,
                actual_positions,
            ):
                if _is_finite_number(expected_value) and _is_finite_number(
                    actual_value
                ):
                    max_error = max(max_error, abs(expected_value - actual_value))
        return max_error

    @staticmethod
    def _is_position_reached(
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> bool:
        for side in ("left", "right"):
            expected_positions = expected.get(f"{side}_pos")
            actual_positions = actual.get(f"{side}_pos")
            if not (
                isinstance(expected_positions, list)
                and isinstance(actual_positions, list)
                and len(expected_positions) == 6
                and len(actual_positions) == 6
            ):
                return False
            for expected_value, actual_value in zip(
                expected_positions,
                actual_positions,
            ):
                if not (
                    _is_finite_number(expected_value)
                    and _is_finite_number(actual_value)
                    and abs(expected_value - actual_value) <= POSITION_TOLERANCE
                ):
                    return False
        return True

    def _emit(self, line: str, stream: str = "stdout"):
        try:
            self.output(line, stream)
        except Exception:
            pass


def hand_fatigue_summary(stats: HandFatigueStats) -> list[str]:
    state_success = stats.state_request_sent - stats.state_timeout
    return [
        "================ 灵巧手疲劳测试汇总 ================",
        (
            f"时长: {stats.elapsed_seconds:.1f}s | 轮次: {stats.loop_rounds} | "
            f"停止原因: {stats.stop_reason or '异常终止'}"
        ),
        (
            f"控制命令: {stats.command_sent} | ACK成功: {stats.set_ack_success} | "
            f"ACK超时: {stats.set_ack_timeout} | 电机失败: {stats.set_ack_fail_motor} | "
            f"无效命令: {stats.set_ack_fail_invalid_cmd} | 其他失败: {stats.set_ack_other_fail}"
        ),
        (
            f"状态查询: {state_success}/{stats.state_request_sent} | "
            f"到位超时: {stats.arrival_timeout_count} | "
            f"位置偏差: {stats.position_mismatch_count} | "
            f"最大偏差: {stats.position_max_error_rad:.4f}rad"
        ),
        (
            f"位置越界: {stats.position_out_of_range_count} | "
            f"格式异常: {stats.malformed_state_count} | "
            f"安全停止: {'已发送' if stats.stop_sent else '发送失败'}"
        ),
        f"异常计数: {stats.failure_count}",
        "====================================================",
    ]