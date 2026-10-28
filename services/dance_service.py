"""Dance, synchronous motion, walking, sequence, and execution-count service."""
import json
from dataclasses import dataclass
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from config import ROBOT_CONFIG
from workers.mcp_worker import McpWorker
from database.repository import DanceCountRepository, SequenceRepository
from models.dance import SequenceStep, DanceSequence

KNOWN_MOTIONS = [
    "stand", "this_way_please", "bow", "wave_greet_bye",
    "nod", "shake_head", "curtain_bow", "blow_kisses_multi",
    "left_hand_side_heart", "right_hand_side_heart", "hand_heart",
    "high_five", "clap", "warm_up_dance", "swag_dance",
    "idol_dance_1", "idol_dance_2", "power_up_dance",
    "shake_hands", "raise_and_int",
]

UNKNOWN_ROBOT_STATUSES = {"", "?", "Unknown", "Unknow"}
REPEAT_UNSAFE_STATUSES = {
    "zerotorque",
    "damping",
    "damped",
    "dampedexception",
    "sit",
    "standsit",
    "liedown",
    "emergencystop",
}


def _is_repeat_unsafe_status(status: str) -> bool:
    normalized = status.casefold().replace("-", "_")
    return (
        normalized in REPEAT_UNSAFE_STATUSES
        or normalized.startswith("error")
        or normalized.startswith("fall")
    )


@dataclass(frozen=True)
class ResourceContext:
    profile_key: str
    accid: str
    firmware: str
    resource_type: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "profile_key": self.profile_key,
            "accid": self.accid,
            "firmware": self.firmware,
            "resource_type": self.resource_type,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ResourceContext | None":
        if not isinstance(value, dict):
            return None
        return cls(
            str(value.get("profile_key", "")),
            str(value.get("accid", "")),
            str(value.get("firmware", "")),
            str(value.get("resource_type", "")),
        )


class DanceService(QObject):
    dance_list_loaded = pyqtSignal(list)              # [{id, name, english_name, rc_mapping, duration}, ...]
    motion_list_loaded = pyqtSignal(list)              # [{motion_index, motion_name_cn, motion_name_en}, ...]
    dance_executed = pyqtSignal(str, int)              # name, new_count
    motion_executed = pyqtSignal(str, int)             # name, new_count
    dance_target_completed = pyqtSignal(str, int, str)  # name, count, robot_accid
    sequence_step_executed = pyqtSignal(int, int)      # step_index, total_steps
    sequence_finished = pyqtSignal(str)                # sequence_name
    error_occurred = pyqtSignal(str)
    action_state_changed = pyqtSignal(bool, str)        # running, label
    motion_engine_changed = pyqtSignal(bool)

    def __init__(self, mcp_worker: McpWorker, parent=None):
        super().__init__(parent)
        self._mcp = mcp_worker
        self._count_repo = DanceCountRepository()
        self._seq_repo = SequenceRepository()
        self._counts: dict[tuple[str, str], int] = {}
        self._dances: list[dict] = []
        self._motions: list[dict] = []
        self._resource_context: ResourceContext | None = None
        self._robot_status = ""
        self._authorized_action: tuple[ResourceContext, str, str, int] | None = None
        self._active_sequence: DanceSequence | None = None
        self._current_step_index = 0
        self._sequence_generation = 0
        self._pending_name = ""
        self._pending_type = ""
        self._busy = False
        self._motion_engine_request: int | None = None
        self._repeat_motion_name = ""
        self._repeat_motion_remaining = 0
        self._repeat_motion_total = 0
        self._repeat_motion_done = 0
        self._repeat_motion_delay_ms = 2000
        self._repeat_motion_completion_timeout: float | None = None
        self._repeat_generation = 0
        self._repeat_motion_in_flight = False
        self._repeat_stop_requested = False
        self._repeat_request_context: dict[str, int | str] | None = None
        self._action_recovery_generation = 0
        self._action_recovery_pending = False

        self._mcp.tool_result_ready.connect(self._on_tool_result)
        self._mcp.tool_error.connect(
            lambda name, error: self.error_occurred.emit(f"{name}: {error}")
        )
        self._mcp.tool_error_context.connect(self._on_tool_error_context)

    # ---- Load ----

    def load_dances(self):
        if not self._resource_context:
            self.error_occurred.emit("机器人资源会话尚未就绪")
            return
        self._mcp.call_tool(
            "get_dances", {}, self._resource_request_context("dance").to_dict(),
        )

    def load_motions(self):
        if not self._resource_context:
            self.error_occurred.emit("机器人资源会话尚未就绪")
            return
        self._mcp.call_tool(
            "get_motions", {}, self._resource_request_context("motion").to_dict(),
        )

    def switch_resource_context(
        self,
        profile_key: str,
        accid: str,
        firmware: str = "",
    ):
        next_context = (
            ResourceContext(profile_key, accid, firmware)
            if profile_key and accid else None
        )
        if next_context == self._resource_context:
            return
        previous_context = self._resource_context
        same_target = bool(
            previous_context
            and next_context
            and previous_context.profile_key == next_context.profile_key
            and previous_context.accid == next_context.accid
        )
        action_was_busy = self._busy
        if not same_target:
            self._mcp.invalidate_pending_requests()
        self._resource_context = next_context
        self._robot_status = ""
        self._authorized_action = None
        self._dances = []
        self._motions = []
        self.dance_list_loaded.emit([])
        self.motion_list_loaded.emit([])
        if same_target:
            if not action_was_busy:
                self.action_state_changed.emit(False, "资源版本已更新")
            return

        self._sequence_generation += 1
        self._active_sequence = None
        self._action_recovery_generation += 1
        self._action_recovery_pending = False
        self._clear_repeat_motion()
        if action_was_busy:
            self._busy = True
            self.error_occurred.emit(
                "动作执行期间机器人目标发生切换，旧目标状态无法确认；"
                "已锁定动作入口，请现场确认旧机器人安全后重启应用"
            )
            self.action_state_changed.emit(
                True,
                "目标已切换，旧机器人动作状态未确认",
            )
            return
        self._busy = False
        self.action_state_changed.emit(False, "资源会话已切换")

    def update_robot_status(self, info: dict):
        next_status = str(info.get("robot_status", "")).strip()
        if next_status in UNKNOWN_ROBOT_STATUSES:
            return
        if (
            self._repeat_motion_name
            and next_status not in {"Walk", "Menu"}
            and not _is_repeat_unsafe_status(next_status)
        ):
            return
        self._robot_status = next_status
        if self._robot_status not in {"Walk", "Menu"}:
            self._authorized_action = None
            if (
                self._resource_context
                and self._resource_context.profile_key == "hu_l04_01"
                and self._repeat_motion_name
                and _is_repeat_unsafe_status(self._robot_status)
            ):
                if self._repeat_motion_in_flight:
                    cancelled = bool(
                        self._repeat_request_context
                        and self._mcp.cancel_pending_request(
                            "execute_motion",
                            self._repeat_request_context,
                        )
                    )
                    if cancelled:
                        self._repeat_motion_in_flight = False
                        self._abort_repeat_for_luna_status()
                    else:
                        self._repeat_stop_requested = True
                        self._repeat_generation += 1
                        self.action_state_changed.emit(
                            True,
                            f"安全状态 {self._robot_status} 触发停止: "
                            f"{self._repeat_motion_name} "
                            f"({self._repeat_motion_done}/{self._repeat_motion_total})",
                        )
                else:
                    self._abort_repeat_for_luna_status()

    def authorize_next_action(
        self,
        action_type: str,
        name: str,
        repeat_count: int = 1,
    ) -> bool:
        if not self._resource_context:
            self.error_occurred.emit("机器人资源会话尚未就绪")
            return False
        if self._busy:
            self._authorized_action = None
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return False
        if (
            action_type not in {"dance", "motion"}
            or not name
            or not isinstance(repeat_count, int)
            or isinstance(repeat_count, bool)
            or not 1 <= repeat_count <= 100
        ):
            self.error_occurred.emit("动作授权参数无效")
            return False
        if (
            self._resource_context.profile_key == "hu_l04_01"
            and not self._luna_action_state_ready(action_type)
        ):
            self._emit_luna_action_state_error(action_type)
            return False
        self._authorized_action = (
            self._resource_context,
            action_type,
            name,
            repeat_count,
        )
        return True

    def _action_execution_ready(
        self,
        action_type: str,
        name: str,
        repeat_count: int = 1,
    ) -> bool:
        if (
            self._resource_context
            and self._resource_context.profile_key == "hu_l04_01"
        ):
            if not self._luna_action_state_ready(action_type):
                self._emit_luna_action_state_error(action_type)
                return False
            expected = (
                self._resource_context,
                action_type,
                name,
                repeat_count,
            )
            if self._authorized_action != expected:
                self.error_occurred.emit("Luna L04 动作执行需要重新确认现场安全")
                return False
            self._authorized_action = None
        return True

    def _luna_action_state_ready(self, action_type: str) -> bool:
        return self._robot_status == "Walk" or (
            action_type == "motion" and self._robot_status == "Menu"
        )

    def _emit_luna_action_state_error(self, action_type: str):
        allowed = "Walk 或 Menu" if action_type == "motion" else "Walk"
        self.error_occurred.emit(
            f"Luna L04 当前状态 {self._robot_status or '未知'}，"
            f"仅允许在 {allowed} 状态执行该操作"
        )

    def _reject_luna_extended_action(self, operation: str) -> bool:
        if (
            self._resource_context
            and self._resource_context.profile_key == "hu_l04_01"
        ):
            self.error_occurred.emit(f"Luna L04 尚未开放{operation}")
            return True
        return False

    def _resource_request_context(self, resource_type: str) -> ResourceContext:
        if not self._resource_context:
            return ResourceContext("", "", "", resource_type)
        return ResourceContext(
            self._resource_context.profile_key,
            self._resource_context.accid,
            self._resource_context.firmware,
            resource_type,
        )

    # ---- Execute ----

    def execute_dance(self, rc_mapping: str, from_sequence: bool = False):
        if self._busy and not from_sequence:
            self._authorized_action = None
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return
        if not self._action_execution_ready("dance", rc_mapping):
            return
        self._pending_name = rc_mapping
        self._pending_type = "dance"
        self._busy = True
        self.action_state_changed.emit(True, f"舞蹈执行中: {rc_mapping}")
        self._mcp.call_tool("execute_dance", {"dance_name": rc_mapping})

    def execute_motion(
        self,
        name: str,
        from_sequence: bool = False,
        completion_timeout: float | None = None,
    ):
        if self._busy and not from_sequence:
            self._authorized_action = None
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return
        if not self._action_execution_ready("motion", name):
            return
        self._clear_repeat_motion()
        self._pending_name = name
        self._pending_type = "motion"
        self._busy = True
        self.action_state_changed.emit(True, f"动作执行中: {name}")
        arguments = {"motion_name": name}
        if completion_timeout is not None:
            arguments["completion_timeout"] = completion_timeout
        self._mcp.call_tool("execute_motion", arguments)

    def execute_motion_repeat(
        self,
        name: str,
        times: int = 5,
        delay_ms: int = 5000,
        completion_timeout: float | None = None,
    ):
        if (
            not isinstance(times, int)
            or isinstance(times, bool)
            or not 1 <= times <= 100
        ):
            self.error_occurred.emit("连续动作次数必须为 1 到 100 的整数")
            return
        repeat_count = times
        if self._busy:
            self._authorized_action = None
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return
        if not self._action_execution_ready("motion", name, repeat_count):
            return
        self._repeat_motion_name = name
        self._repeat_motion_remaining = repeat_count
        self._repeat_motion_total = self._repeat_motion_remaining
        self._repeat_motion_done = 0
        self._repeat_motion_delay_ms = max(1000, int(delay_ms))
        self._repeat_motion_completion_timeout = completion_timeout
        self._repeat_generation += 1
        repeat_generation = self._repeat_generation
        self._repeat_motion_in_flight = False
        self._repeat_stop_requested = False
        self._busy = True
        self._send_next_repeat_motion(repeat_generation)

    def stop_motion_repeat(self):
        if not self._repeat_motion_name:
            return
        stopped_name = self._repeat_motion_name
        done = self._repeat_motion_done
        total = self._repeat_motion_total
        if self._repeat_motion_in_flight:
            self._repeat_stop_requested = True
            self._repeat_generation += 1
            self.action_state_changed.emit(
                True,
                f"用户请求停止连续动作: {stopped_name} ({done}/{total})",
            )
            return
        self._clear_repeat_motion()
        self._busy = False
        self.action_state_changed.emit(False, f"连续动作已停止: {stopped_name} ({done}/{total})")

    def _send_next_repeat_motion(self, repeat_generation: int | None = None):
        if (
            repeat_generation is not None
            and repeat_generation != self._repeat_generation
        ):
            return
        if not self._repeat_motion_name:
            return
        if (
            self._resource_context
            and self._resource_context.profile_key == "hu_l04_01"
            and not self._luna_action_state_ready("motion")
        ):
            self._abort_repeat_for_luna_status()
            return
        if self._repeat_motion_remaining <= 0:
            self._finish_repeat_motion()
            return
        current = self._repeat_motion_done + 1
        total = self._repeat_motion_total
        self._pending_name = self._repeat_motion_name
        self._pending_type = "motion"
        self._repeat_motion_remaining -= 1
        self._repeat_motion_in_flight = True
        self._repeat_request_context = {
            "kind": "repeat_motion",
            "generation": self._repeat_generation,
        }
        self.action_state_changed.emit(True, f"连续动作执行中: {self._repeat_motion_name} ({current}/{total})")
        self._mcp.call_tool(
            "execute_motion",
            {
                "motion_name": self._repeat_motion_name,
                **(
                    {"completion_timeout": self._repeat_motion_completion_timeout}
                    if self._repeat_motion_completion_timeout is not None else {}
                ),
            },
            self._repeat_request_context,
        )

    def _abort_repeat_for_luna_status(self):
        stopped_name = self._repeat_motion_name
        done = self._repeat_motion_done
        total = self._repeat_motion_total
        self._clear_repeat_motion()
        self._busy = False
        self._emit_luna_action_state_error("motion")
        self.action_state_changed.emit(
            False,
            f"连续动作中止: {stopped_name} ({done}/{total})",
        )

    def _finish_repeat_motion(self):
        finished_name = self._repeat_motion_name
        total = self._repeat_motion_total
        self._clear_repeat_motion()
        self._busy = False
        self.action_state_changed.emit(False, f"连续动作完成: {finished_name} ({total}/{total})")

    def _clear_repeat_motion(self):
        self._repeat_generation += 1
        self._repeat_motion_name = ""
        self._repeat_motion_remaining = 0
        self._repeat_motion_total = 0
        self._repeat_motion_done = 0
        self._repeat_motion_completion_timeout = None
        self._repeat_motion_in_flight = False
        self._repeat_stop_requested = False
        self._repeat_request_context = None

    def set_walk_velocity(
        self,
        x: float,
        y: float,
        yaw: float,
        sequence_generation: int | None = None,
    ):
        if self._reject_luna_extended_action("行走控制"):
            return
        request_context = (
            {"sequence_generation": sequence_generation}
            if sequence_generation is not None else None
        )
        self._mcp.call_tool(
            "set_walk_velocity",
            {"x": x, "y": y, "yaw": yaw},
            request_context,
        )

    def set_motion_engine(self, mode: int = 1):
        if self._reject_luna_extended_action("手动动作库模式"):
            return
        self._motion_engine_request = mode
        self._mcp.call_tool("set_motion_engine", {"mode": mode})

    # ---- Count tracking ----

    def get_count(self, name: str) -> int:
        key = (ROBOT_CONFIG.ws_accid, name)
        if key not in self._counts:
            self._counts[key] = self._count_repo.get_count(ROBOT_CONFIG.ws_accid, name)
        return self._counts[key]

    def _increment_count(self, name: str, category: str) -> int:
        robot_accid = ROBOT_CONFIG.ws_accid
        new_count = self._count_repo.increment(robot_accid, name, category)
        self._counts[(robot_accid, name)] = new_count
        return new_count

    def load_all_counts(self):
        for row in self._count_repo.get_all_counts():
            self._counts[(row.get("robot_accid", "__legacy__"), row["name"])] = row["count"]

    # ---- Sequence management ----

    def save_sequence(self, name: str, steps: list[SequenceStep]):
        steps_json = json.dumps([s.to_dict() for s in steps])
        self._seq_repo.save(name, steps_json)

    def load_sequences(self) -> list[DanceSequence]:
        results = []
        for row in self._seq_repo.load_all():
            steps_data = json.loads(row["steps_json"])
            results.append(DanceSequence(
                name=row["name"],
                steps=[SequenceStep.from_dict(s) for s in steps_data],
                created_at=row.get("created_at"),
            ))
        return results

    def delete_sequence(self, seq_id: int):
        self._seq_repo.delete(seq_id)

    def execute_sequence(self, sequence: DanceSequence):
        if self._reject_luna_extended_action("序列器"):
            return
        if self._busy:
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再运行序列")
            return
        self._sequence_generation += 1
        self._active_sequence = sequence
        self._current_step_index = 0
        self._busy = True
        self.action_state_changed.emit(True, f"序列执行中: {sequence.name}")
        self._execute_next_step(self._sequence_generation)

    def _execute_next_step(self, sequence_generation: int | None = None):
        if (
            self._active_sequence is None
            or (
                sequence_generation is not None
                and sequence_generation != self._sequence_generation
            )
        ):
            return
        if self._current_step_index >= len(self._active_sequence.steps):
            sequence_name = self._active_sequence.name
            self.sequence_finished.emit(sequence_name)
            self._active_sequence = None
            self._busy = False
            self.action_state_changed.emit(False, f"序列完成: {sequence_name}")
            return

        step = self._active_sequence.steps[self._current_step_index]
        if step.type == "dance":
            self.execute_dance(step.name, from_sequence=True)
        elif step.type == "motion":
            self.execute_motion(step.name, from_sequence=True)
        elif step.type == "walk":
            self.set_walk_velocity(
                step.vx,
                step.vy,
                step.omega,
                self._sequence_generation,
            )

        self.sequence_step_executed.emit(
            self._current_step_index + 1, len(self._active_sequence.steps))

        if step.type in {"dance", "motion", "walk"}:
            return

        if step.delay_ms > 0:
            current_generation = self._sequence_generation
            QTimer.singleShot(
                step.delay_ms,
                lambda generation=current_generation: self._advance_and_continue(generation),
            )
        else:
            self._advance_and_continue(self._sequence_generation)

    def _advance_and_continue(self, sequence_generation: int | None = None):
        if (
            self._active_sequence is None
            or (
                sequence_generation is not None
                and sequence_generation != self._sequence_generation
            )
        ):
            return
        self._current_step_index += 1
        self._execute_next_step(self._sequence_generation)

    # ---- MCP result handlers ----

    def _on_tool_result(self, tool_name: str, result):
        target_context = result.get("_target_context", {}) if isinstance(result, dict) else {}
        request_context = (
            target_context.get("request_context", {})
            if isinstance(target_context, dict) else {}
        )
        if (
            tool_name in {"get_action_library_status", "recover_action_walk"}
            and isinstance(request_context, dict)
            and request_context.get("kind") == "action_recovery"
        ):
            if self._matches_current_target(target_context):
                self._on_action_recovery_result(
                    tool_name,
                    result,
                    request_context,
                )
            return
        if tool_name in {
            "execute_dance",
            "execute_motion",
            "set_motion_engine",
            "set_walk_velocity",
        }:
            if not self._matches_current_target(target_context):
                return
        if tool_name == "execute_motion" and self._repeat_motion_name:
            if target_context.get("request_context") != self._repeat_request_context:
                return
        response_context = ResourceContext.from_dict(
            target_context.get("request_context") if isinstance(target_context, dict) else None
        )
        if tool_name in {"get_dances", "get_motions"}:
            expected_type = "dance" if tool_name == "get_dances" else "motion"
            if (
                not response_context
                or response_context != self._resource_request_context(expected_type)
            ):
                return
        if tool_name == "get_dances":
            content = result.get("content", [])
            if content and isinstance(content[0], str):
                try:
                    data = json.loads(content[0])
                    if isinstance(data, dict) and "dances" in data:
                        self._dances = data["dances"]
                    elif isinstance(data, list):
                        self._dances = data
                except json.JSONDecodeError:
                    pass
            self.dance_list_loaded.emit(self._dances)

        elif tool_name == "get_motions":
            content = result.get("content", [])
            if content and isinstance(content[0], str):
                try:
                    data = json.loads(content[0])
                    motions = data.get("motion_list", [])
                    self._motions = motions
                    self.motion_list_loaded.emit(self._motions)
                except json.JSONDecodeError:
                    pass

        elif tool_name == "execute_dance":
            sequence_active = self._active_sequence is not None
            if self._action_recovery_required(result):
                self._begin_action_recovery("舞蹈执行状态未确认")
                return
            self._emit_restore_warning(result)
            if result.get("success"):
                count = self._increment_count(self._pending_name, "dance")
                self.dance_executed.emit(self._pending_name, count)
                if count == 20:
                    self.dance_target_completed.emit(self._pending_name, count, ROBOT_CONFIG.ws_accid)
            else:
                self.error_occurred.emit(f"舞蹈 {self._pending_name} 执行未完成: {result.get('content', ['未知错误'])[0]}")
                self._sequence_generation += 1
                self._active_sequence = None
            if sequence_active and result.get("success"):
                self._advance_sequence_after_action()
                return
            self._busy = False
            self.action_state_changed.emit(False, self._action_completion_label(result))

        elif tool_name == "execute_motion":
            sequence_active = self._active_sequence is not None
            if self._action_recovery_required(result):
                self._begin_action_recovery("动作执行状态未确认")
                return
            self._emit_restore_warning(result)
            if self._repeat_motion_name:
                self._repeat_motion_in_flight = False
                self._repeat_request_context = None
            if result.get("success"):
                action_data = self._action_result_data(result)
                post_action = action_data.get("post_action", {})
                if (
                    post_action.get("verified_mode") == "remote_control"
                    and post_action.get("verified_state") == "idle"
                ):
                    self._robot_status = "Walk"
                count = self._increment_count(self._pending_name, "motion")
                self.motion_executed.emit(self._pending_name, count)
                if self._repeat_motion_name:
                    self._repeat_motion_done += 1
                    if self._repeat_stop_requested:
                        stopped_name = self._repeat_motion_name
                        done = self._repeat_motion_done
                        total = self._repeat_motion_total
                        self._clear_repeat_motion()
                        self._busy = False
                        self.action_state_changed.emit(
                            False,
                            f"连续动作已停止: {stopped_name} ({done}/{total})",
                        )
                        return
                    if self._repeat_motion_remaining > 0:
                        if (
                            self._resource_context
                            and self._resource_context.profile_key == "hu_l04_01"
                            and not self._luna_action_state_ready("motion")
                        ):
                            self._abort_repeat_for_luna_status()
                            return
                        self.action_state_changed.emit(
                            True,
                            f"连续动作等待中: {self._repeat_motion_name} ({self._repeat_motion_done}/{self._repeat_motion_total})，{self._repeat_motion_delay_ms // 1000}秒后继续",
                        )
                        repeat_generation = self._repeat_generation
                        QTimer.singleShot(
                            self._repeat_motion_delay_ms,
                            lambda generation=repeat_generation: self._send_next_repeat_motion(generation),
                        )
                    else:
                        self._finish_repeat_motion()
                    return
            else:
                self.error_occurred.emit(f"动作 {self._pending_name} 执行未完成: {result.get('content', ['未知错误'])[0]}")
                if self._repeat_motion_name:
                    failed_name = self._repeat_motion_name
                    done = self._repeat_motion_done
                    total = self._repeat_motion_total
                    self._clear_repeat_motion()
                    self._busy = False
                    self.action_state_changed.emit(False, f"连续动作中止: {failed_name} ({done}/{total})")
                    return
                self._sequence_generation += 1
                self._active_sequence = None
            if sequence_active and result.get("success"):
                self._advance_sequence_after_action()
                return
            self._busy = False
            self.action_state_changed.emit(False, self._action_completion_label(result))

        elif tool_name == "set_walk_velocity":
            request_context = (
                target_context.get("request_context", {})
                if isinstance(target_context, dict) else {}
            )
            sequence_generation = (
                request_context.get("sequence_generation")
                if isinstance(request_context, dict) else None
            )
            if (
                sequence_generation is None
                or sequence_generation != self._sequence_generation
                or self._active_sequence is None
            ):
                return
            if not result.get("success"):
                self.error_occurred.emit(
                    f"序列行走步骤执行失败: {result.get('content', ['未知错误'])[0]}"
                )
                self._abort_sequence()
                return
            self._advance_sequence_after_action()

        elif tool_name == "set_motion_engine" and result.get("success"):
            if self._motion_engine_request is not None:
                self.motion_engine_changed.emit(self._motion_engine_request == 1)

    def _matches_current_target(self, target_context: object) -> bool:
        if not isinstance(target_context, dict) or not self._resource_context:
            return False
        return (
            target_context.get("generation") == self._mcp.target_generation
            and target_context.get("accid") == self._resource_context.accid
            and target_context.get("profile_key") == self._resource_context.profile_key
        )

    def _advance_sequence_after_action(self):
        if self._active_sequence is None:
            return
        step = self._active_sequence.steps[self._current_step_index]
        if step.delay_ms > 0:
            self.action_state_changed.emit(
                True,
                f"序列等待中: {self._active_sequence.name}",
            )
            current_generation = self._sequence_generation
            QTimer.singleShot(
                step.delay_ms,
                lambda generation=current_generation: self._advance_and_continue(generation),
            )
        else:
            self._advance_and_continue(self._sequence_generation)

    def _on_tool_error_context(
        self,
        tool_name: str,
        error: str,
        target_context: object,
    ):
        del error
        if not self._matches_current_target(target_context):
            return
        request_context = (
            target_context.get("request_context", {})
            if isinstance(target_context, dict) else {}
        )
        if (
            tool_name in {"get_action_library_status", "recover_action_walk"}
            and isinstance(request_context, dict)
            and request_context.get("kind") == "action_recovery"
        ):
            self._schedule_action_recovery_check(
                int(request_context.get("generation", -1)),
            )
            return
        if (
            tool_name == "set_walk_velocity"
            and self._active_sequence is not None
        ):
            self._abort_sequence()
            return
        if tool_name not in {"execute_dance", "execute_motion"} or not self._busy:
            return
        if (
            tool_name == "execute_motion"
            and self._repeat_motion_name
            and target_context.get("request_context") != self._repeat_request_context
        ):
            return
        self._begin_action_recovery("动作请求异常，执行状态未确认")
        return

    def _action_recovery_required(self, result: dict) -> bool:
        post_action = self._action_result_data(result).get("post_action", {})
        return bool(
            post_action.get("recovery") == "pending"
            or post_action.get("verified_state") in {"running", "unknown"}
        )

    def _begin_action_recovery(self, reason: str):
        self._authorized_action = None
        self._sequence_generation += 1
        self._active_sequence = None
        self._clear_repeat_motion()
        self._action_recovery_generation += 1
        generation = self._action_recovery_generation
        self._action_recovery_pending = True
        self._busy = True
        self.error_occurred.emit(f"{reason}，已锁定动作入口并等待机器人空闲")
        self.action_state_changed.emit(
            True,
            "动作状态未确认，等待机器人空闲",
        )
        self._schedule_action_recovery_check(generation, delay_ms=0)

    def _schedule_action_recovery_check(
        self,
        generation: int,
        delay_ms: int = 1500,
    ):
        if (
            not self._action_recovery_pending
            or generation != self._action_recovery_generation
        ):
            return
        QTimer.singleShot(
            delay_ms,
            lambda current_generation=generation: self._request_action_recovery_status(
                current_generation
            ),
        )

    def _request_action_recovery_status(self, generation: int):
        if (
            not self._action_recovery_pending
            or generation != self._action_recovery_generation
        ):
            return
        self._mcp.call_tool(
            "get_action_library_status",
            {},
            {
                "kind": "action_recovery",
                "generation": generation,
            },
        )

    def _on_action_recovery_result(
        self,
        tool_name: str,
        result: dict,
        request_context: dict,
    ):
        generation = int(request_context.get("generation", -1))
        if (
            not self._action_recovery_pending
            or generation != self._action_recovery_generation
        ):
            return
        data = self._action_result_data(result)
        if tool_name == "recover_action_walk":
            if (
                result.get("success")
                and data.get("verified_mode") == "remote_control"
                and data.get("verified_state") == "idle"
            ):
                self._finish_action_recovery()
                return
            self._schedule_action_recovery_check(generation)
            return

        mode = data.get("action_library_mode")
        state = data.get("action_library_state")
        if result.get("success") and mode == "remote_control" and state == "idle":
            self._finish_action_recovery()
            return
        if result.get("success") and mode == "action_library" and state == "idle":
            self._mcp.call_tool(
                "recover_action_walk",
                {},
                request_context,
            )
            return
        self._schedule_action_recovery_check(generation)

    def _finish_action_recovery(self):
        self._action_recovery_pending = False
        self._busy = False
        self._robot_status = "Walk"
        self.action_state_changed.emit(False, "已确认回到拟人行走模式")

    def _abort_sequence(self):
        sequence_name = self._active_sequence.name if self._active_sequence else ""
        self._sequence_generation += 1
        self._active_sequence = None
        self._busy = False
        self.action_state_changed.emit(False, f"序列中止: {sequence_name}")

    def _emit_restore_warning(self, result: dict):
        data = self._action_result_data(result)
        post_action = data.get("post_action", {})
        if post_action and not (
            post_action.get("verified_mode") == "remote_control"
            and post_action.get("verified_state") == "idle"
        ):
            self.error_occurred.emit(f"动作已完成，但自动切回拟人行走模式失败: {post_action}")

    def _action_completion_label(self, result: dict) -> str:
        post_action = self._action_result_data(result).get("post_action", {})
        if post_action:
            if (
                post_action.get("verified_mode") == "remote_control"
                and post_action.get("verified_state") == "idle"
            ):
                return "已回到拟人行走模式"
            return "动作结束，但未确认恢复拟人行走模式"
        return "动作执行完成" if result.get("success") else "动作执行已停止"

    @staticmethod
    def _action_result_data(result: dict) -> dict:
        content = result.get("content", [])
        if not content or not isinstance(content[0], str):
            return {}
        try:
            data = json.loads(content[0])
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    # ---- Accessors ----

    @property
    def resource_context(self) -> ResourceContext | None:
        return self._resource_context

    @property
    def dances(self) -> list[dict]:
        return self._dances

    @property
    def motions(self) -> list[dict]:
        return self._motions
