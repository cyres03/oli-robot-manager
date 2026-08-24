from datetime import datetime
import os
from pathlib import Path
import stat

from PyQt6.QtCore import QObject, pyqtSignal

from config import ROBOT_CONFIG
from models.robot_profile import L04_PROFILE, OLI_PROFILE
from models.managed_case import TestRunResult as RunResult, TestRunStatus as RunStatus
from services.managed_test_service import TestCaseService as ManagedTestCaseService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeWorker(QObject):
    output_line = pyqtSignal(str, str)
    completed = pyqtSignal(object)
    authentication_required = pyqtSignal(str, str, str)
    instances = []

    def __init__(self, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.kwargs = kwargs
        self.cancelled = False
        self.running = False
        self.waited = False
        self.wait_calls = []
        self.wait_results = []
        FakeWorker.instances.append(self)

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def wait(self, timeout=None):
        self.waited = True
        self.wait_calls.append(timeout)
        result = self.wait_results.pop(0) if self.wait_results else True
        if result:
            self.running = False
        return result


def _service(tmp_path, monkeypatch):
    import services.managed_test_service as module

    FakeWorker.instances.clear()
    monkeypatch.setattr(module, "TestCaseWorker", FakeWorker)
    return ManagedTestCaseService(
        PROJECT_ROOT / "resources/test_cases/cases.json",
        PROJECT_ROOT / "resources/test_cases",
        result_root=tmp_path,
    )


def test_service_filters_cases_and_uses_profile_node(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.apply_context(L04_PROFILE, "HU_L04_01_090", "v1")

    service.run_case("luna-speech-vision-snapshot", approved=True)

    worker = FakeWorker.instances[-1]
    assert worker.kwargs["profile"] is L04_PROFILE
    assert worker.kwargs["accid"] == "HU_L04_01_090"
    assert worker.kwargs["firmware"] == "v1"
    assert worker.kwargs["case"].target_role == "speech_vision"


def test_context_switch_cancels_worker_and_drops_old_result(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.apply_context(L04_PROFILE, "HU_L04_01_090", "v1")
    results = []
    service.run_finished.connect(results.append)
    service.run_case("luna-main-snapshot", approved=True)
    worker = FakeWorker.instances[-1]
    generation = service._generation
    case = worker.kwargs["case"]

    service.apply_context(OLI_PROFILE, "HU_D04_01_001", "v2")
    result = RunResult.create(
        session_id="old-session",
        case=case,
        accid="HU_L04_01_090",
        firmware="v1",
        target_host="10.192.1.2",
        status=RunStatus.PASS,
        started_at=datetime.now(),
    )
    service._on_completed(generation, result)

    assert worker.cancelled is True
    assert results == []
    assert not (tmp_path / "old-session").exists()


def test_service_persists_current_result(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.apply_context(L04_PROFILE, "HU_L04_01_090", "v1")
    service.run_case("luna-main-snapshot", approved=True)
    worker = FakeWorker.instances[-1]
    case = worker.kwargs["case"]
    result = RunResult.create(
        session_id="new-session",
        case=case,
        accid="HU_L04_01_090",
        firmware="v1",
        target_host="10.192.1.2",
        status=RunStatus.PASS,
        started_at=datetime.now(),
        detail="ok",
    )

    service._on_completed(service._generation, result)

    result_file = tmp_path / "new-session" / "result.json"
    assert result_file.is_file()
    assert '"status": "PASS"' in result_file.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(result_file.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(result_file.stat().st_mode) == 0o600


def test_late_authorization_request_is_dropped_after_context_switch(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.apply_context(L04_PROFILE, "HU_L04_01_090", "v1")
    requests = []
    service.ssh_authorization_required.connect(lambda *args: requests.append(args))
    service.run_case("luna-main-snapshot", approved=True)
    worker = FakeWorker.instances[-1]

    service.apply_context(OLI_PROFILE, "HU_D04_01_001", "v2")
    worker.authentication_required.emit(
        "10.192.1.2", "limx", "HU_L04_01_090",
    )

    assert requests == []
    assert service._pending_authorization is None


def test_shutdown_cancels_and_waits_for_running_worker(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.apply_context(L04_PROFILE, "HU_L04_01_090", "v1")
    service.run_case("luna-main-snapshot", approved=True)
    worker = FakeWorker.instances[-1]

    service.shutdown()

    assert worker.cancelled is True
    assert worker.waited is True
    assert worker.wait_calls == [15000]


def test_shutdown_waits_until_worker_stops_after_timeout(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.apply_context(L04_PROFILE, "HU_L04_01_090", "v1")
    service.run_case("luna-main-snapshot", approved=True)
    worker = FakeWorker.instances[-1]
    worker.wait_results = [False, True]
    errors = []
    service.error_occurred.connect(errors.append)

    service.shutdown()

    assert worker.cancelled is True
    assert worker.wait_calls == [15000, None]
    assert errors == ["测试任务仍在退出，应用将等待其安全停止"]