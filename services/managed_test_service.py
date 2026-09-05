"""Lifecycle and persistence for managed robot test cases."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from config import APP_CONFIG, ROBOT_CONFIG
from models.robot_profile import CapabilityState, RobotProfile
from models.managed_case import (
    TestCaseDefinition,
    TestRunResult,
    TestRunStatus,
    TestSource,
    load_test_cases,
)
from workers.hand_fatigue_worker import HandFatigueWorker
from workers.managed_test_worker import TestCaseWorker


class TestCaseService(QObject):
    cases_changed = pyqtSignal(list)
    run_started = pyqtSignal(object)
    output_line = pyqtSignal(str, str)
    run_finished = pyqtSignal(object)
    error_occurred = pyqtSignal(str)
    ssh_authorization_required = pyqtSignal(str, str, str, str)

    def __init__(
        self,
        manifest_path: Path,
        resource_root: Path,
        result_root: Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.manifest_path = manifest_path
        self.resource_root = resource_root
        self.result_root = result_root or Path(APP_CONFIG.data_dir) / "test-runs"
        self._all_cases = load_test_cases(manifest_path)
        self._profile: RobotProfile | None = None
        self._accid = ""
        self._firmware = "unknown"
        self._generation = 0
        self._worker: TestCaseWorker | HandFatigueWorker | None = None
        self._pending_authorization: (
            tuple[int, str, bool, Path | None, tuple[str, ...] | None] | None
        ) = None

    def apply_context(
        self,
        profile: RobotProfile | None,
        accid: str,
        firmware: str = "unknown",
    ):
        next_key = profile.key if profile else ""
        if (
            self._profile
            and self._profile.key == next_key
            and self._accid == accid
            and self._firmware == firmware
        ):
            return
        self.cancel_current()
        self._generation += 1
        self._profile = profile
        self._accid = accid
        self._firmware = firmware or "unknown"
        self._pending_authorization = None
        self.cases_changed.emit(self.available_cases())

    def available_cases(self) -> list[TestCaseDefinition]:
        if not self._profile:
            return []
        return [
            case for case in self._all_cases
            if case.product_key == self._profile.key
            and (
                not case.required_capability
                or self._profile.capability(case.required_capability)
                == CapabilityState.SUPPORTED
            )
        ]

    def run_case(
        self,
        case_id: str,
        approved: bool = False,
        local_script_path: Path | None = None,
        arguments_override: tuple[str, ...] | None = None,
    ):
        if self._worker and self._worker.isRunning():
            self.error_occurred.emit("已有测试用例正在执行")
            return
        if not self._profile or not self._accid:
            self.error_occurred.emit("机器人身份或测试工作区未就绪")
            return
        case = next(
            (item for item in self.available_cases() if item.case_id == case_id),
            None,
        )
        if case is None:
            self.error_occurred.emit(f"当前型号不支持测试用例 {case_id}")
            return
        try:
            case = self._with_arguments(case, arguments_override)
            case.validate_approval(approved)
        except (PermissionError, ValueError) as error:
            self.error_occurred.emit(str(error))
            return

        node = self._resolve_node(case)
        if node is None:
            self.error_occurred.emit(f"当前型号没有节点角色 {case.target_role}")
            return
        generation = self._generation
        if case.source == TestSource.BUILTIN_RUNNER:
            worker = HandFatigueWorker(
                case=case,
                profile=self._profile,
                accid=self._accid,
                firmware=self._firmware,
                approved=approved,
                generation=generation,
                parent=self,
            )
        else:
            passwords = (
                list(ROBOT_CONFIG.main_control_passwords)
                if node.role == "main"
                else [ROBOT_CONFIG.perception_password]
            )
            worker = TestCaseWorker(
                case=case,
                profile=self._profile,
                accid=self._accid,
                firmware=self._firmware,
                passwords=passwords,
                result_root=self.result_root,
                resource_root=self.resource_root,
                local_script_path=local_script_path,
                approved=approved,
                generation=generation,
                parent=self,
            )
        worker.output_line.connect(
            lambda line, stream, current=generation:
            self._emit_if_current(current, self.output_line, line, stream)
        )
        worker.completed.connect(
            lambda result, current=generation:
            self._on_completed(current, result)
        )
        if case.source != TestSource.BUILTIN_RUNNER:
            worker.authentication_required.connect(
                lambda host, username, robot_id, current=generation:
                self._on_authentication_required(
                    current, host, username, robot_id,
                    case.case_id, approved, local_script_path, arguments_override,
                )
            )
        self._worker = worker
        self.run_started.emit(case)
        worker.start()

    def cancel_current(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    def shutdown(self):
        worker = self._worker
        self.cancel_current()
        if not worker or not worker.isRunning():
            return
        if worker.wait(15000):
            return
        self.error_occurred.emit("测试任务仍在退出，应用将等待其安全停止")
        worker.cancel()
        worker.wait()

    def retry_after_authorization(self, case_id: str):
        pending = self._pending_authorization
        self._pending_authorization = None
        if not pending:
            return
        generation, pending_case_id, approved, local_path, arguments = pending
        if generation != self._generation or pending_case_id != case_id:
            return
        self._worker = None
        self.run_case(case_id, approved, local_path, arguments)

    def cancel_authorization(self, case_id: str, detail: str):
        pending = self._pending_authorization
        self._pending_authorization = None
        if pending and pending[1] == case_id:
            self._worker = None
            self.error_occurred.emit(detail)

    def _resolve_node(self, case: TestCaseDefinition):
        if not self._profile:
            return None
        nodes = (self._profile.main_node, *self._profile.companion_nodes)
        return next((node for node in nodes if node.role == case.target_role), None)

    def _on_completed(self, generation: int, result: TestRunResult):
        if generation != self._generation:
            return
        self._worker = None
        self._persist_result(result)
        self.run_finished.emit(result)

    def _on_authentication_required(
        self,
        generation: int,
        host: str,
        username: str,
        robot_id: str,
        case_id: str,
        approved: bool,
        local_path: Path | None,
        arguments: tuple[str, ...] | None,
    ):
        if generation != self._generation:
            return
        self._pending_authorization = (
            generation, case_id, approved, local_path, arguments,
        )
        self._worker = None
        self.ssh_authorization_required.emit(
            host, username, robot_id, case_id,
        )

    def _persist_result(self, result: TestRunResult):
        session_dir = self.result_root / result.session_id
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(session_dir, 0o700)
        payload = asdict(result)
        payload["status"] = result.status.value
        result_path = session_dir / "result.json"
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(result_path, 0o600)

    @staticmethod
    def _with_arguments(
        case: TestCaseDefinition,
        arguments: tuple[str, ...] | None,
    ) -> TestCaseDefinition:
        if arguments is None:
            return case
        if case.source.value == "remote_command":
            raise ValueError("远端命令用例不允许覆盖参数")
        values = tuple(str(value) for value in arguments)
        if len(values) > 16:
            raise ValueError("测试参数不能超过 16 个")
        if any(
            len(value) > 256 or "\0" in value or "\n" in value or "\r" in value
            for value in values
        ):
            raise ValueError("测试参数包含无效字符或长度超过 256")
        return replace(case, arguments=values)

    def _emit_if_current(self, generation: int, signal, *args):
        if generation == self._generation:
            signal.emit(*args)