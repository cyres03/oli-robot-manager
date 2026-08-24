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
        self._dances = []
        self._motions = []
        self._active_sequence = None
        self._clear_repeat_motion()
        self._busy = False
        self.dance_list_loaded.emit([])
        self.motion_list_loaded.emit([])
        self.action_state_changed.emit(False, "资源会话已切换")

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
        if self._busy and self._active_sequence is None:
            self.error_occurred.emit("当前已有舞蹈/动作在执行，请等待完成后再操作")
            return
        self._pending_name = rc_mapping
        self._pending_type = "dance"
        self._busy = True
        self.action_state_changed.emit(True, f"舞蹈执行中: {rc_mapping}")
        self._mcp.call_tool("execute_dance", {"dance_name": rc_mapping})

    def execute_motion(self, name: str):
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
        self._mcp.call_tool("set_walk_velocity", {"x": x, "y": y, "yaw": yaw})

    def set_motion_engine(self, mode: int = 1):
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
            if result.get("success"):
                count = self._increment_count(self._pending_name, "dance")
                self.dance_executed.emit(self._pending_name, count)
                if count == 20:
                    self.dance_target_completed.emit(self._pending_name, count, ROBOT_CONFIG.ws_accid)
                self._emit_restore_warning(result)
            else:
                self.error_occurred.emit(f"舞蹈 {self._pending_name} 执行未完成: {result.get('content', ['未知错误'])[0]}")
                self._active_sequence = None
            self._busy = False
            self.action_state_changed.emit(False, "已回到拟人行走模式")
            if self._active_sequence and result.get("success"):
                self._advance_sequence_after_action()

        elif tool_name == "execute_motion":
            if result.get("success"):
                count = self._increment_count(self._pending_name, "motion")
                self.motion_executed.emit(self._pending_name, count)
                self._emit_restore_warning(result)
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
            self.action_state_changed.emit(False, "已回到拟人行走模式")
            if self._active_sequence and result.get("success"):
                self._advance_sequence_after_action()

        elif tool_name == "set_motion_engine" and result.get("success"):
            if self._motion_engine_request is not None:
                self.motion_engine_changed.emit(self._motion_engine_request == 1)

    def _advance_sequence_after_action(self):
        if self._active_sequence is None:
            return
        step = self._active_sequence.steps[self._current_step_index]
        if step.delay_ms > 0:
            QTimer.singleShot(step.delay_ms, self._advance_and_continue)
        else:
            self._advance_and_continue()

    def _emit_restore_warning(self, result: dict):
        content = result.get("content", [])
        if not content or not isinstance(content[0], str):
            return
        try:
            data = json.loads(content[0])
        except json.JSONDecodeError:
            return
        post_action = data.get("post_action", {})
        if post_action and post_action.get("set_walk_mode") != "success":
            self.error_occurred.emit(f"动作已完成，但自动切回拟人行走模式失败: {post_action}")

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
