from dataclasses import replace

from models.managed_case import (
    HAND_FATIGUE_CAPABILITY,
    HAND_FATIGUE_RUNNER,
    TestCaseDefinition as CaseDefinition,
    TestRisk as Risk,
    TestRunStatus as RunStatus,
    TestSource as Source,
)
from models.robot_profile import L04_PROFILE, OLI_PROFILE
from services.hand_fatigue_runner import HandFatigueStats
from workers.hand_fatigue_worker import HandFatigueWorker


def _case(product_key="hu_l04_01", arguments=("7200", "10")):
    return CaseDefinition(
        case_id=f"{product_key}-hand-fatigue",
        name="双灵巧手疲劳测试",
        product_key=product_key,
        target_role="main",
        source=Source.BUILTIN_RUNNER,
        category="灵巧手",
        timeout_seconds=7500,
        runner=HAND_FATIGUE_RUNNER,
        required_capability=HAND_FATIGUE_CAPABILITY,
        arguments=arguments,
        risks=frozenset({Risk.HARDWARE_CONTROL}),
    )


class SuccessfulRunner:
    instances = []

    def __init__(self, ws_url, accid, config, cancel_event, output):
        self.ws_url = ws_url
        self.accid = accid
        self.config = config
        self.cancel_event = cancel_event
        self.output = output
        self.stats = HandFatigueStats()
        SuccessfulRunner.instances.append(self)

    async def run(self):
        self.output("phase complete", "stdout")
        self.stats.stop_sent = True
        self.stats.stop_reason = "达到设定测试时长"
        self.stats.completed_monotonic = self.stats.started_monotonic + 7200
        return self.stats


def _run(worker):
    results = []
    worker.completed.connect(results.append)
    worker.run()
    return results[0]


def test_worker_uses_profile_websocket_and_full_luna_accid():
    SuccessfulRunner.instances.clear()
    worker = HandFatigueWorker(
        case=_case(),
        profile=L04_PROFILE,
        accid="HU_L04_01_093",
        firmware="v1",
        approved=True,
        runner_factory=SuccessfulRunner,
    )

    result = _run(worker)

    runner = SuccessfulRunner.instances[-1]
    assert runner.ws_url == "ws://10.192.1.2:5000"
    assert runner.accid == "HU_L04_01_093"
    assert runner.config.duration_seconds == 7200
    assert runner.config.cycles_per_phase == 10
    assert result.status == RunStatus.PASS
    assert result.accid == "HU_L04_01_093"
    assert "phase complete" in result.stdout


def test_worker_supports_oli_profile_with_same_runner():
    SuccessfulRunner.instances.clear()
    worker = HandFatigueWorker(
        case=_case("oli", ("60", "2")),
        profile=OLI_PROFILE,
        accid="HU_D04_01_099",
        firmware="v2",
        approved=True,
        runner_factory=SuccessfulRunner,
    )

    result = _run(worker)

    runner = SuccessfulRunner.instances[-1]
    assert runner.accid == "HU_D04_01_099"
    assert runner.config.duration_seconds == 60
    assert runner.config.cycles_per_phase == 2
    assert result.status == RunStatus.PASS


def test_worker_rejects_accid_from_another_profile_before_connecting():
    SuccessfulRunner.instances.clear()
    worker = HandFatigueWorker(
        case=_case(),
        profile=L04_PROFILE,
        accid="HU_D04_01_099",
        firmware="v1",
        approved=True,
        runner_factory=SuccessfulRunner,
    )

    result = _run(worker)

    assert SuccessfulRunner.instances == []
    assert result.status == RunStatus.ERROR
    assert "ACCID 与型号不匹配" in result.detail


def test_worker_rejects_profile_without_explicit_capability():
    SuccessfulRunner.instances.clear()
    profile = replace(
        L04_PROFILE,
        capabilities=tuple(
            item for item in L04_PROFILE.capabilities
            if item[0] != HAND_FATIGUE_CAPABILITY
        ),
    )
    worker = HandFatigueWorker(
        case=_case(),
        profile=profile,
        accid="HU_L04_01_093",
        firmware="v1",
        approved=True,
        runner_factory=SuccessfulRunner,
    )

    result = _run(worker)

    assert SuccessfulRunner.instances == []
    assert result.status == RunStatus.ERROR
    assert "未启用能力 hand_fatigue" in result.detail


def test_worker_marks_cancelled_result_after_runner_safely_stops():
    class CancelledRunner(SuccessfulRunner):
        async def run(self):
            self.cancel_event.set()
            self.stats.stop_sent = True
            self.stats.stop_reason = "用户取消或机器人目标已切换"
            return self.stats

    worker = HandFatigueWorker(
        case=_case(),
        profile=L04_PROFILE,
        accid="HU_L04_01_093",
        firmware="v1",
        approved=True,
        runner_factory=CancelledRunner,
    )

    result = _run(worker)

    assert result.status == RunStatus.CANCELLED
    assert "安全停止: 已发送" in result.stdout


def test_worker_enforces_case_timeout_and_waits_for_runner_cleanup():
    class SlowRunner(SuccessfulRunner):
        async def run(self):
            import asyncio

            try:
                await asyncio.sleep(60)
            finally:
                self.stats.stop_sent = True

    case = replace(
        _case(arguments=("1", "1")),
        timeout_seconds=0.01,
    )
    worker = HandFatigueWorker(
        case=case,
        profile=L04_PROFILE,
        accid="HU_L04_01_093",
        firmware="v1",
        approved=True,
        runner_factory=SlowRunner,
    )

    result = _run(worker)

    assert result.status == RunStatus.ERROR
    assert "测试超过 0.01 秒" in result.detail
    assert "安全停止发送失败" not in result.detail