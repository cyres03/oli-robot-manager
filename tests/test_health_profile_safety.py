from config import ROBOT_CONFIG
from services.health_check_service import HealthCheckService


class OutputWorker:
    def __init__(self, output: str):
        self.collected_output = output


def test_l04_cpu_mismatch_does_not_run_repair(monkeypatch):
    service = HealthCheckService()
    repairs = []
    next_steps = []
    monkeypatch.setattr(ROBOT_CONFIG, "expected_cpu_cores", 8)
    monkeypatch.setattr(ROBOT_CONFIG, "allow_cpu_repair", False)
    monkeypatch.setattr(service, "_fix_cpu", lambda: repairs.append(True))
    monkeypatch.setattr(service, "_check_camera", lambda: next_steps.append("camera"))

    service._on_cpu_output(0, OutputWorker("4"))

    assert repairs == []
    assert next_steps == ["camera"]


def test_l04_time_mismatch_does_not_run_repair(monkeypatch):
    service = HealthCheckService()
    repairs = []
    next_steps = []
    monkeypatch.setattr(ROBOT_CONFIG, "allow_time_repair", False)
    monkeypatch.setattr(service, "_fix_time", lambda: repairs.append(True))
    monkeypatch.setattr(service, "_check_imu", lambda: next_steps.append("imu"))

    service._on_time_output(0, OutputWorker("2000-01-01 00:00:00"))

    assert repairs == []
    assert next_steps == ["imu"]