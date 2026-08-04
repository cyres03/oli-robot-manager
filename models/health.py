from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json


@dataclass
class CpuCheckResult:
    detected_cores: int = 0
    expected_cores: int = 8
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "detected_cores": self.detected_cores,
            "expected_cores": self.expected_cores,
            "passed": self.passed,
        }


@dataclass
class TimeCheckResult:
    robot_time: Optional[str] = None
    local_time: Optional[str] = None
    diff_seconds: float = 0.0
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "robot_time": self.robot_time,
            "local_time": self.local_time,
            "diff_seconds": self.diff_seconds,
            "passed": self.passed,
        }


@dataclass
class ImuCheckResult:
    detected_frequency: float = 0.0
    expected_frequency: float = 500.0
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "detected_frequency": self.detected_frequency,
            "expected_frequency": self.expected_frequency,
            "passed": self.passed,
        }


@dataclass
class CameraCheckResult:
    camera_count: int = 0
    expected_count: int = 2
    usb3_detected: bool = False
    consistent: bool = False       # All 3 checks returned same count
    passed: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "camera_count": self.camera_count,
            "expected_count": self.expected_count,
            "usb3_detected": self.usb3_detected,
            "consistent": self.consistent,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class HealthCheckResult:
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    wifi_connected: bool = False
    cpu_result: Optional[CpuCheckResult] = None
    camera_result: Optional[CameraCheckResult] = None
    time_result: Optional[TimeCheckResult] = None
    imu_result: Optional[ImuCheckResult] = None
    all_passed: bool = False

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "wifi_connected": self.wifi_connected,
            "cpu_result": self.cpu_result.to_dict() if self.cpu_result else None,
            "camera_result": self.camera_result.to_dict() if self.camera_result else None,
            "time_result": self.time_result.to_dict() if self.time_result else None,
            "imu_result": self.imu_result.to_dict() if self.imu_result else None,
            "all_passed": self.all_passed,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "HealthCheckResult":
        cpu = CpuCheckResult(**d["cpu_result"]) if d.get("cpu_result") else None
        cam = CameraCheckResult(**d["camera_result"]) if d.get("camera_result") else None
        time_res = TimeCheckResult(**d["time_result"]) if d.get("time_result") else None
        imu = ImuCheckResult(**d["imu_result"]) if d.get("imu_result") else None
        return cls(
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            wifi_connected=d.get("wifi_connected", False),
            cpu_result=cpu,
            camera_result=cam,
            time_result=time_res,
            imu_result=imu,
            all_passed=d.get("all_passed", False),
        )
