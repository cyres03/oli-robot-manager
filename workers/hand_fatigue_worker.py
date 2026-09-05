"""Qt worker for the built-in BrainCo dual-hand fatigue test."""
import asyncio
from datetime import datetime
import threading
import uuid

from PyQt6.QtCore import QThread, pyqtSignal

from models.managed_case import (
    HAND_FATIGUE_CAPABILITY,
    HAND_FATIGUE_RUNNER,
    TestCaseDefinition,
    TestRunResult,
    TestRunStatus,
    TestSource,
)
from models.robot_profile import CapabilityState, RobotProfile
from services.hand_fatigue_runner import (
    HandFatigueConfig,
    HandFatigueRunner,
    HandFatigueStats,
    hand_fatigue_summary,
)


MAX_CAPTURE_BYTES = 1024 * 1024


class HandFatigueRunTimeout(TimeoutError):
    pass


class HandFatigueWorker(QThread):
    output_line = pyqtSignal(str, str)
    completed = pyqtSignal(object)

    def __init__(
        self,
        case: TestCaseDefinition,
        profile: RobotProfile,
        accid: str,
        firmware: str,
        approved: bool = False,
        generation: int = 0,
        runner_factory=None,
        parent=None,
    ):
        super().__init__(parent)
        self.case = case
        self.profile = profile
        self.accid = accid
        self.firmware = firmware
        self.approved = approved
        self.generation = generation
        self.runner_factory = runner_factory or HandFatigueRunner
        self.session_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        )
        self._cancel_event = threading.Event()
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._captured_bytes = 0

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        started_at = datetime.now()
        target_host = self.profile.main_node.host
        runner = None
        stats = None
        try:
            config = self._validate_and_build_config()
            ws_url = self.profile.service("websocket").url or ""
            runner = self.runner_factory(
                ws_url,
                self.accid,
                config,
                self._cancel_event,
                self._capture_output,
            )
            stats = asyncio.run(self._run_with_timeout(runner))
            self._emit_summary(stats)
            if self._cancel_event.is_set():
                status = TestRunStatus.CANCELLED
                detail = "测试已取消"
            elif stats.failure_count:
                status = TestRunStatus.FAIL
                detail = f"检测到 {stats.failure_count} 项协议或运动异常"
            else:
                status = TestRunStatus.PASS
                detail = "测试通过"
            if not stats.stop_sent:
                detail += f"; 双手安全停止发送失败: {stats.stop_error or '未知错误'}"
            result = self._result(
                started_at,
                target_host,
                status,
                detail,
            )
        except HandFatigueRunTimeout:
            if runner is not None:
                stats = runner.stats
                self._emit_summary(stats)
            detail = f"测试超过 {self.case.timeout_seconds} 秒，已停止"
            if stats is not None and not stats.stop_sent:
                detail += f"; 双手安全停止发送失败: {stats.stop_error or '连接不可用'}"
            result = self._result(
                started_at,
                target_host,
                TestRunStatus.ERROR,
                detail,
            )
        except Exception as error:
            if runner is not None:
                stats = runner.stats
                self._emit_summary(stats)
            status = (
                TestRunStatus.CANCELLED
                if self._cancel_event.is_set()
                else TestRunStatus.ERROR
            )
            detail = "测试已取消" if status == TestRunStatus.CANCELLED else str(error)
            if stats is not None and not stats.stop_sent:
                detail += f"; 双手安全停止发送失败: {stats.stop_error or '连接不可用'}"
            result = self._result(
                started_at,
                target_host,
                status,
                detail,
            )
        self.completed.emit(result)

    async def _run_with_timeout(self, runner) -> HandFatigueStats:
        task = asyncio.create_task(runner.run())
        done, _ = await asyncio.wait(
            (task,),
            timeout=self.case.timeout_seconds,
        )
        if task in done:
            return task.result()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise HandFatigueRunTimeout

    def _validate_and_build_config(self) -> HandFatigueConfig:
        if self.profile.key != self.case.product_key:
            raise ValueError(
                f"用例 {self.case.case_id} 属于 {self.case.product_key}，"
                f"当前型号为 {self.profile.key}"
            )
        if not self.accid or not self.profile.matches(self.accid):
            raise ValueError("当前机器人 ACCID 与型号不匹配")
        if self.case.source != TestSource.BUILTIN_RUNNER:
            raise ValueError("灵巧手 Worker 仅支持应用内置运行器")
        if self.case.runner != HAND_FATIGUE_RUNNER:
            raise ValueError(f"不支持的应用内置运行器: {self.case.runner}")
        if self.case.target_role != "main":
            raise ValueError("灵巧手疲劳测试只能使用 main 节点")
        if self.case.required_capability != HAND_FATIGUE_CAPABILITY:
            raise ValueError("灵巧手疲劳用例缺少能力声明")
        if (
            self.profile.capability(self.case.required_capability)
            != CapabilityState.SUPPORTED
        ):
            raise ValueError(
                f"当前型号未启用能力 {self.case.required_capability}"
            )
        if not self.profile.service("websocket").supported:
            raise ValueError("当前型号未配置 WebSocket 服务")
        if not self.case.supports_firmware(self.firmware):
            raise ValueError(
                f"测试用例 {self.case.case_id} 不支持固件 {self.firmware}"
            )
        self.case.validate_approval(self.approved)

        arguments = self.case.arguments or ("7200", "10")
        if len(arguments) != 2:
            raise ValueError("灵巧手疲劳测试参数应为时长秒数和每阶段循环次数")
        try:
            duration_seconds = float(arguments[0])
            cycles_per_phase = int(arguments[1])
        except ValueError as error:
            raise ValueError("灵巧手疲劳测试参数格式无效") from error
        maximum_duration = max(1, self.case.timeout_seconds - 30)
        if duration_seconds > maximum_duration:
            raise ValueError(
                f"疲劳测试时长不能超过 {maximum_duration} 秒"
            )
        config = HandFatigueConfig(
            duration_seconds=duration_seconds,
            cycles_per_phase=cycles_per_phase,
        )
        config.validate()
        return config

    def _capture_output(self, line: str, stream: str):
        text = str(line)
        encoded_size = len(text.encode("utf-8", errors="replace")) + 1
        if self._captured_bytes + encoded_size <= MAX_CAPTURE_BYTES:
            target = self._stderr if stream == "stderr" else self._stdout
            target.append(text)
            self._captured_bytes += encoded_size
        self.output_line.emit(text, stream)

    def _emit_summary(self, stats: HandFatigueStats):
        for line in hand_fatigue_summary(stats):
            self._capture_output(line, "stdout")
        for sample in stats.anomaly_samples[:20]:
            self._capture_output(f"[异常] {sample}", "stderr")

    def _result(
        self,
        started_at: datetime,
        target_host: str,
        status: TestRunStatus,
        detail: str,
    ) -> TestRunResult:
        return TestRunResult.create(
            session_id=self.session_id,
            case=self.case,
            accid=self.accid,
            firmware=self.firmware,
            target_host=target_host,
            status=status,
            started_at=started_at,
            stdout="\n".join(self._stdout),
            stderr="\n".join(self._stderr),
            detail=detail,
        )