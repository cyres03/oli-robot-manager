"""
Dance & motion library service.
- Dances: request_get_dance_list / request_dance (rc_mapping) / notify_dance
- Motions: request_get_atomic_motion_list / request_execute_atomic_motion / notify_execute_atomic_motion
- Walking: request_set_walk_vel
- Tracking execution counts in-memory + SQLite
"""
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
    count_reset = pyqtSignal(str, str, int)            # name, category, new_count
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
        self._counts: dict[tuple[str, str, str], int] = {}
        self._dances: list[dict] = []
        self._motions: list[dict] = []
        self._resource_context: ResourceContext | None = None
        self._robot_status = ""
        self._authorized_action: tuple[ResourceContext, str, str] | None = None
        self._active_sequence: DanceSequence | None = None
        self._current_step_index = 0
        self._pending_name = ""
        self._pending_type = ""
        self._busy = False
        self._motion_engine_request: int | None = None
        self._repeat_motion_name = ""
        self._repeat_motion_remaining = 0
        self._repeat_motion_total = 0
        self._repeat_motion_done = 0
        self._repeat_motion_delay_ms = 2000

        self._mcp.tool_result_ready.connect(self._on_tool_result)
        self._mcp.tool_error.connect(lambda n, e: self.error_occurred.emit(f"{n}: {e}"))

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
        self._resource_context = next_context
        self._robot_status = ""
        self._authorized_action = None
        self._dances = []
        self._motions = []
        self._active_sequence = None
        self._clear_repeat_motion()
        self._busy = False
        self.dance_list_loaded.emit([])
        self.motion_list_loaded.emit([])
        self.action_state_changed.emit(False, "资源会话已切换")

    def update_robot_status(self, info: dict):
        self._robot_status = str(info.get("robot_status", ""))
        if self._robot_status != "Walk":
            self._authorized_action = None

    def authorize_next_action(self, action_type: str, name: str) -> bool:
        if not self._resource_context:
            self.error_occurred.emit("机器人资源会话尚未就绪")
            return False
        if action_type not in {"dance", "motion"} or not name:
            self.error_occurred.emit("动作授权参数无效")
            return False
        if (
            self._resource_context.profile_key == "hu_l04_01"
            and self._robot_status != "Walk"
        ):
            self._emit_luna_walk_error()
            return False
        self._authorized_action = (self._resource_context, action_type, name)
        return True

    def _action_execution_ready(self, action_type: str, name: str) -> bool:
        if (
            self._resource_context
            and self._resource_context.profile_key == "hu_l04_01"
        ):
            if self._robot_status != "Walk":
                self._emit_luna_walk_error()
                return False
            expected = (self._resource_context, action_type, name)
            if self._authorized_action != expected:
                self.error_occurred.emit("Luna L04 单次动作需要重新确认现场安全")
                return False
            self._authorized_action = None
        return True

    def _emit_luna_walk_error(self):
        self.error_occurred.emit(
            f"Luna L04 当前状态 {self._robot_status or '未知'}，"
            "仅允许在 Walk 状态执行动作"
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

    def execute_dance(self, rc_mapping: str):
        if not self._action_execution_ready("dance", rc_mapping):
            return
        if self._busy and self._active_sequence is None:
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return
        self._pending_name = rc_mapping
        self._pending_type = "dance"
        self._busy = True
        self.action_state_changed.emit(True, f"舞蹈执行中: {rc_mapping}")
        self._mcp.call_tool("execute_dance", {"dance_name": rc_mapping})

    def execute_motion(self, name: str):
        if not self._action_execution_ready("motion", name):
            return
        if self._busy and self._active_sequence is None:
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return
        self._repeat_motion_name = ""
        self._repeat_motion_remaining = 0
        self._repeat_motion_total = 0
        self._repeat_motion_done = 0
        self._pending_name = name
        self._pending_type = "motion"
        self._busy = True
        self.action_state_changed.emit(True, f"动作执行中: {name}")
        self._mcp.call_tool("execute_motion", {"motion_name": name})

    def execute_motion_repeat(self, name: str, times: int = 5, delay_ms: int = 5000):
        if self._reject_luna_extended_action("连续动作"):
            return
        if self._busy:
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return
        self._repeat_motion_name = name
        self._repeat_motion_remaining = max(0, int(times))
        self._repeat_motion_total = self._repeat_motion_remaining
        self._repeat_motion_done = 0
        self._repeat_motion_delay_ms = max(1000, int(delay_ms))
        self._busy = True
        self._send_next_repeat_motion()

    def stop_motion_repeat(self):
        if not self._repeat_motion_name:
            return
        stopped_name = self._repeat_motion_name
        done = self._repeat_motion_done
        total = self._repeat_motion_total
        self._clear_repeat_motion()
        self._busy = False
        self.action_state_changed.emit(False, f"连续动作已停止: {stopped_name} ({done}/{total})")

    def _send_next_repeat_motion(self):
        if not self._repeat_motion_name:
            return
        if self._repeat_motion_remaining <= 0:
            self._finish_repeat_motion()
            return
        current = self._repeat_motion_done + 1
        total = self._repeat_motion_total
        self._pending_name = self._repeat_motion_name
        self._pending_type = "motion"
        self._repeat_motion_remaining -= 1
        self.action_state_changed.emit(True, f"连续动作执行中: {self._repeat_motion_name} ({current}/{total})")
        self._mcp.call_tool("execute_motion", {"motion_name": self._repeat_motion_name})

    def _finish_repeat_motion(self):
        finished_name = self._repeat_motion_name
        total = self._repeat_motion_total
        self._clear_repeat_motion()
        self._busy = False
        self.action_state_changed.emit(False, f"连续动作完成: {finished_name} ({total}/{total})")

    def _clear_repeat_motion(self):
        self._repeat_motion_name = ""
        self._repeat_motion_remaining = 0
        self._repeat_motion_total = 0
        self._repeat_motion_done = 0

    def set_walk_velocity(self, x: float, y: float, yaw: float):
        if self._reject_luna_extended_action("行走控制"):
            return
        self._mcp.call_tool("set_walk_velocity", {"x": x, "y": y, "yaw": yaw})

    def set_motion_engine(self, mode: int = 1):
        if self._reject_luna_extended_action("手动动作库模式"):
            return
        self._motion_engine_request = mode
        self._mcp.call_tool("set_motion_engine", {"mode": mode})

    # ---- Count tracking ----

    def get_count(self, name: str, category: str) -> int:
        key = (ROBOT_CONFIG.ws_accid, name, category)
        if key not in self._counts:
            self._counts[key] = self._count_repo.get_count(
                ROBOT_CONFIG.ws_accid, name, category,
            )
        return self._counts[key]

    def _increment_count(self, name: str, category: str) -> int:
        robot_accid = ROBOT_CONFIG.ws_accid
        new_count = self._count_repo.increment(robot_accid, name, category)
        self._counts[(robot_accid, name, category)] = new_count
        return new_count

    def reset_count(self, name: str, category: str) -> bool:
        robot_accid = ROBOT_CONFIG.ws_accid
        try:
            reset_succeeded = self._count_repo.reset(robot_accid, name, category)
        except Exception as exc:
            self.error_occurred.emit(f"清零 {name} 执行次数失败: {exc}")
            return False
        if not reset_succeeded:
            self.error_occurred.emit(f"未找到 {name} 的执行次数记录")
            return False
        self._counts[(robot_accid, name, category)] = 0
        self.count_reset.emit(name, category, 0)
        return True

    def load_all_counts(self):
        for row in self._count_repo.get_all_counts():
            self._counts[
                (row.get("robot_accid", "__legacy__"), row["name"], row["category"])
            ] = row["count"]

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
        self._active_sequence = sequence
        self._current_step_index = 0
        self._execute_next_step()

    def _execute_next_step(self):
        if self._active_sequence is None:
            return
        if self._current_step_index >= len(self._active_sequence.steps):
            self.sequence_finished.emit(self._active_sequence.name)
            self._active_sequence = None
            return

        step = self._active_sequence.steps[self._current_step_index]
        if step.type == "dance":
            self.execute_dance(step.name)
        elif step.type == "motion":
            self.execute_motion(step.name)
        elif step.type == "walk":
            self.set_walk_velocity(step.vx, step.vy, step.omega)

        self.sequence_step_executed.emit(
            self._current_step_index + 1, len(self._active_sequence.steps))

        if step.type in {"dance", "motion"}:
            return

        if step.delay_ms > 0:
            QTimer.singleShot(step.delay_ms, self._advance_and_continue)
        else:
            self._current_step_index += 1
            self._execute_next_step()

    def _advance_and_continue(self):
        self._current_step_index += 1
        self._execute_next_step()

    # ---- MCP result handlers ----

    def _on_tool_result(self, tool_name: str, result):
        target_context = result.get("_target_context", {}) if isinstance(result, dict) else {}
        if tool_name in {"execute_dance", "execute_motion", "set_motion_engine"}:
            if not self._matches_current_target(target_context):
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
            self._emit_restore_warning(result)
            if result.get("success"):
                count = self._increment_count(self._pending_name, "dance")
                self.dance_executed.emit(self._pending_name, count)
                if count == 20:
                    self.dance_target_completed.emit(self._pending_name, count, ROBOT_CONFIG.ws_accid)
            else:
                self.error_occurred.emit(f"舞蹈 {self._pending_name} 执行未完成: {result.get('content', ['未知错误'])[0]}")
                self._active_sequence = None
            self._busy = False
            self.action_state_changed.emit(False, self._action_completion_label(result))
            if self._active_sequence and result.get("success"):
                self._advance_sequence_after_action()

        elif tool_name == "execute_motion":
            self._emit_restore_warning(result)
            if result.get("success"):
                count = self._increment_count(self._pending_name, "motion")
                self.motion_executed.emit(self._pending_name, count)
                if self._repeat_motion_name:
                    self._repeat_motion_done += 1
                    if self._repeat_motion_remaining > 0:
                        self.action_state_changed.emit(
                            True,
                            f"连续动作等待中: {self._repeat_motion_name} ({self._repeat_motion_done}/{self._repeat_motion_total})，{self._repeat_motion_delay_ms // 1000}秒后继续",
                        )
                        QTimer.singleShot(self._repeat_motion_delay_ms, self._send_next_repeat_motion)
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
                self._active_sequence = None
            self._busy = False
            self.action_state_changed.emit(False, self._action_completion_label(result))
            if self._active_sequence and result.get("success"):
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
            QTimer.singleShot(step.delay_ms, self._advance_and_continue)
        else:
            self._advance_and_continue()

    def _emit_restore_warning(self, result: dict):
        data = self._action_result_data(result)
        post_action = data.get("post_action", {})
        if post_action and (
            post_action.get("exit_motion_engine") != "success"
            or post_action.get("set_walk_mode") != "success"
        ):
            self.error_occurred.emit(f"动作已完成，但自动切回拟人行走模式失败: {post_action}")

    def _action_completion_label(self, result: dict) -> str:
        post_action = self._action_result_data(result).get("post_action", {})
        if post_action:
            if (
                post_action.get("exit_motion_engine") == "success"
                and post_action.get("set_walk_mode") == "success"
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
