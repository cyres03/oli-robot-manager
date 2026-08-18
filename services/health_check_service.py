"""
State machine implementing the health check diagnostic:
IDLE → CONNECTING_WIFI → CHECKING_CPU → [FIXING_CPU →] CHECKING_CAMERA → CHECKING_TIME → [FIXING_TIME →] CHECKING_IMU → COMPLETED
"""
import re
from enum import Enum, auto
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from workers.ssh_worker import SshWorker
from workers.wifi_worker import WifiWorker
from network.wifi_manager import WifiManager
from models.health import (
    HealthCheckResult, CpuCheckResult, CameraCheckResult,
    TimeCheckResult, ImuCheckResult,
)
from config import ROBOT_CONFIG


class HealthCheckState(Enum):
    IDLE = auto()
    CONNECTING_WIFI = auto()
    CHECKING_CPU = auto()
    FIXING_CPU = auto()
    CHECKING_CAMERA = auto()
    CHECKING_TIME = auto()
    FIXING_TIME = auto()
    CHECKING_IMU = auto()
    COMPLETED = auto()


class HealthCheckService(QObject):
    step_started = pyqtSignal(HealthCheckState, str)
    step_result = pyqtSignal(HealthCheckState, dict)
    diagnostic_complete = pyqtSignal(HealthCheckResult)
    diagnostic_error = pyqtSignal(str)
    ssh_authorization_required = pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = HealthCheckState.IDLE
        self._result = HealthCheckResult()
        self._retry_count = 0
        self._workers: list = []
        self._ssh_retry = None

    def run_full_diagnostic(self):
        self._result = HealthCheckResult(started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._retry_count = 0
        self._workers.clear()
        self._ssh_retry = None
        self._transition_to(HealthCheckState.CONNECTING_WIFI)
        self._check_wifi()

    def _transition_to(self, state: HealthCheckState):
        self._state = state
        descriptions = {
            HealthCheckState.CONNECTING_WIFI: "连接机器人WiFi...",
            HealthCheckState.CHECKING_CPU: "检查感知电脑CPU核心数...",
            HealthCheckState.FIXING_CPU: "修复CPU核心数（删除错误DTB并重启）...",
            HealthCheckState.CHECKING_CAMERA: "检查双目相机USB连接...",
            HealthCheckState.CHECKING_TIME: "检查主控系统时间...",
            HealthCheckState.FIXING_TIME: "校正系统时间...",
            HealthCheckState.CHECKING_IMU: "检查IMU频率...",
            HealthCheckState.COMPLETED: "诊断完成",
        }
        self.step_started.emit(state, descriptions.get(state, ""))

    # ---- Step 1: WiFi ----

    def _check_wifi(self):
        if WifiManager.is_robot_wifi():
            ssid = WifiManager.get_robot_ssid() or WifiManager.get_current_ssid()
            self._result.wifi_connected = True
            self.step_result.emit(HealthCheckState.CONNECTING_WIFI, {"passed": True, "ssid": ssid})
            self._transition_to(HealthCheckState.CHECKING_CPU)
            self._check_cpu()
            return

        w = WifiWorker()
        w.wifi_connected.connect(self._on_wifi_ok)
        w.wifi_error.connect(self._on_wifi_fail)
        self._workers.append(w)
        w.scan_and_connect_to_robot_wifi()

    def _on_wifi_ok(self, ssid: str):
        self._result.wifi_connected = True
        self.step_result.emit(HealthCheckState.CONNECTING_WIFI, {"passed": True, "ssid": ssid})
        self._transition_to(HealthCheckState.CHECKING_CPU)
        self._check_cpu()

    def _on_wifi_fail(self, err: str):
        self._result.wifi_connected = False
        self.step_result.emit(HealthCheckState.CONNECTING_WIFI, {"passed": False, "error": err})
        # Continue anyway - user is already connected
        self._transition_to(HealthCheckState.CHECKING_CPU)
        self._check_cpu()

    # ---- Step 2: CPU Check ----

    def _check_cpu(self):
        w = SshWorker(ROBOT_CONFIG.perception_ip, ROBOT_CONFIG.perception_user,
                       [ROBOT_CONFIG.perception_password])
        w.set_command("nproc")
        w.command_finished.connect(lambda ec, worker=w: self._on_cpu_output(ec, worker))
        w.error_occurred.connect(lambda e: self._on_ssh_error("CPU", e))
        self._watch_ssh_authentication(w, self._check_cpu)
        self._workers.append(w)
        w.start()

    def _on_cpu_output(self, exit_code: int, worker: SshWorker):
        output = worker.collected_output.strip()
        cores = int(output) if output.isdigit() else 0
        self._result.cpu_result = CpuCheckResult(
            detected_cores=cores,
            expected_cores=ROBOT_CONFIG.expected_cpu_cores,
            passed=(cores == ROBOT_CONFIG.expected_cpu_cores),
        )

        if cores == ROBOT_CONFIG.expected_cpu_cores:
            self.step_result.emit(HealthCheckState.CHECKING_CPU, {"passed": True, "cores": cores})
            self._transition_to(HealthCheckState.CHECKING_CAMERA)
            self._check_camera()
        elif cores > 0 and self._retry_count < ROBOT_CONFIG.cpu_fix_max_retries:
            self._retry_count += 1
            self.step_result.emit(HealthCheckState.CHECKING_CPU,
                {"passed": False, "cores": cores,
                 "message": f"期望8核,实际{cores}核,尝试修复({self._retry_count}/{ROBOT_CONFIG.cpu_fix_max_retries})"})
            self._transition_to(HealthCheckState.FIXING_CPU)
            self._fix_cpu()
        else:
            self.step_result.emit(HealthCheckState.CHECKING_CPU,
                {"passed": False, "cores": cores, "error": "CPU检查失败"})
            self._transition_to(HealthCheckState.CHECKING_CAMERA)
            self._check_camera()

    def _fix_cpu(self):
        w = SshWorker(ROBOT_CONFIG.perception_ip, ROBOT_CONFIG.perception_user,
                       [ROBOT_CONFIG.perception_password])
        w.set_command("sudo rm /boot/dtd/orin_nx_16g.dtb && sudo reboot")
        w.command_finished.connect(lambda ec, worker=w: self._on_fix_done(ec))
        w.error_occurred.connect(lambda e: self._on_ssh_error("FIX_CPU", e))
        self._watch_ssh_authentication(w, self._fix_cpu)
        self._workers.append(w)
        w.start()

    def _on_fix_done(self, exit_code: int):
        self.step_result.emit(HealthCheckState.FIXING_CPU,
            {"message": "DTB已删除,正在重启,60秒后重新检查..."})
        QTimer.singleShot(60000, self._check_cpu)

    def _on_ssh_error(self, step: str, error: str):
        """Handle SSH error by advancing state instead of getting stuck."""
        self.diagnostic_error.emit(f"[{step}] SSH失败: {error}")
        if self._state == HealthCheckState.CHECKING_CPU:
            self._result.cpu_result = CpuCheckResult(passed=False)
            self.step_result.emit(HealthCheckState.CHECKING_CPU,
                {"passed": False, "error": error})
            self._transition_to(HealthCheckState.CHECKING_CAMERA)
            self._check_camera()
        elif self._state == HealthCheckState.FIXING_CPU:
            self._transition_to(HealthCheckState.CHECKING_CAMERA)
            self._check_camera()
        elif self._state == HealthCheckState.CHECKING_CAMERA:
            self._result.camera_result = CameraCheckResult(passed=False)
            self.step_result.emit(HealthCheckState.CHECKING_CAMERA,
                {"passed": False, "error": error})
            self._transition_to(HealthCheckState.CHECKING_TIME)
            self._check_time()
        elif self._state == HealthCheckState.CHECKING_TIME:
            self._result.time_result = TimeCheckResult(passed=False)
            self.step_result.emit(HealthCheckState.CHECKING_TIME,
                {"passed": False, "error": error})
            self._transition_to(HealthCheckState.CHECKING_IMU)
            self._check_imu()
        elif self._state == HealthCheckState.FIXING_TIME:
            self._transition_to(HealthCheckState.CHECKING_IMU)
            self._check_imu()
        elif self._state == HealthCheckState.CHECKING_IMU:
            self._result.imu_result = ImuCheckResult(passed=False)
            self.step_result.emit(HealthCheckState.CHECKING_IMU,
                {"passed": False, "error": error})
            self._transition_to(HealthCheckState.COMPLETED)
            self._finish()

    def _watch_ssh_authentication(self, worker: SshWorker, retry):
        worker.authentication_required.connect(
            lambda host, username, robot_id: self._on_ssh_authentication_required(
                host, username, robot_id, retry
            )
        )

    def _on_ssh_authentication_required(
        self, host: str, username: str, robot_id: str, retry
    ):
        self._ssh_retry = (robot_id, retry)
        self.diagnostic_error.emit("当前机器人需要一次 SSH 密码验证")
        self.ssh_authorization_required.emit(host, username, robot_id)

    def finish_ssh_authorization(self, success: bool, detail: str):
        pending = self._ssh_retry
        self._ssh_retry = None
        if success and pending:
            robot_id, retry = pending
            QTimer.singleShot(
                0, lambda: self._retry_ssh_step(robot_id, retry)
            )
            return
        self._on_ssh_error("AUTHORIZATION", detail)

    def _retry_ssh_step(self, robot_id: str, retry):
        if ROBOT_CONFIG.ws_accid != robot_id:
            self._on_ssh_error(
                "AUTHORIZATION",
                f"机器人已从 {robot_id} 切换为 {ROBOT_CONFIG.ws_accid}，"
                "原检查已取消",
            )
            return
        retry()

    # ---- Step 2.5: Camera Check ----

    def _check_camera(self):
        self._camera_checks: list[int] = []
        self._camera_usb3 = False
        self._camera_raw = ""
        self._run_camera_check(0)

    def _run_camera_check(self, attempt: int):
        if attempt >= 3:
            self._on_camera_done()
            return
        w = SshWorker(ROBOT_CONFIG.perception_ip, ROBOT_CONFIG.perception_user,
                       [ROBOT_CONFIG.perception_password])
        w.set_command("lsusb -t 2>&1; echo '---'; lsusb 2>&1")
        w.command_finished.connect(lambda ec, worker=w, a=attempt: self._on_camera_output(ec, worker, a))
        w.error_occurred.connect(lambda e: self._on_ssh_error("CAMERA", e))
        self._watch_ssh_authentication(
            w, lambda current_attempt=attempt: self._run_camera_check(current_attempt)
        )
        self._workers.append(w)
        w.start()

    def _on_camera_output(self, exit_code: int, worker: SshWorker, attempt: int):
        output = worker.collected_output
        self._camera_raw = output

        # Count cameras from lsusb (look for camera-related devices)
        cam_lines = [l for l in output.splitlines()
                     if any(kw in l.lower() for kw in ("camera", "realsense", "imaging", "video"))]
        self._camera_checks.append(len(cam_lines))

        # Check USB 3.0 (5000M) for stereo cameras
        if "5000" in output or "5000M" in output:
            self._camera_usb3 = True

        # Run next check
        QTimer.singleShot(500, lambda: self._run_camera_check(attempt + 1))

    def _on_camera_done(self):
        counts = self._camera_checks
        consistent = len(set(counts)) == 1 if counts else False
        cam_count = counts[-1] if counts else 0

        summary = f"检测{cam_count}个相机 (3次:{counts}), USB3.0={'是' if self._camera_usb3 else '否'}"
        passed = cam_count >= 2 and self._camera_usb3 and consistent

        self._result.camera_result = CameraCheckResult(
            camera_count=cam_count,
            expected_count=2,
            usb3_detected=self._camera_usb3,
            consistent=consistent,
            passed=passed,
            detail=summary,
        )

        self.step_result.emit(HealthCheckState.CHECKING_CAMERA, {
            "passed": passed,
            "camera_count": cam_count,
            "usb3": self._camera_usb3,
            "consistent": consistent,
            "detail": summary,
        })

        self._transition_to(HealthCheckState.CHECKING_TIME)
        self._check_time()

    # ---- Step 3: Time Check ----

    def _check_time(self):
        w = SshWorker(ROBOT_CONFIG.main_control_ip, ROBOT_CONFIG.main_control_user,
                       ROBOT_CONFIG.main_control_passwords)
        w.set_command("date '+%Y-%m-%d %H:%M:%S'")
        w.command_finished.connect(lambda ec, worker=w: self._on_time_output(ec, worker))
        w.error_occurred.connect(lambda e: self._on_ssh_error("TIME", e))
        self._watch_ssh_authentication(w, self._check_time)
        self._workers.append(w)
        w.start()

    def _on_time_output(self, exit_code: int, worker: SshWorker):
        robot_time_str = worker.collected_output.strip()
        local_now = datetime.now()
        local_str = local_now.strftime("%Y-%m-%d %H:%M:%S")

        passed = False
        diff = 0.0
        try:
            robot_time = datetime.strptime(robot_time_str, "%Y-%m-%d %H:%M:%S")
            diff = abs((robot_time - local_now).total_seconds())
            passed = diff < 60
        except ValueError:
            passed = False

        self._result.time_result = TimeCheckResult(
            robot_time=robot_time_str,
            local_time=local_str,
            diff_seconds=diff,
            passed=passed,
        )

        self.step_result.emit(HealthCheckState.CHECKING_TIME, {
            "passed": passed,
            "robot_time": robot_time_str,
            "local_time": local_str,
            "diff_seconds": diff,
        })

        if not passed:
            self._transition_to(HealthCheckState.FIXING_TIME)
            self._fix_time()
        else:
            self._transition_to(HealthCheckState.CHECKING_IMU)
            self._check_imu()

    def _fix_time(self):
        local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commands = (
            "sudo timedatectl set-timezone Asia/Shanghai && "
            f"sudo date -s '{local_now}' && "
            "sudo hwclock --systohc"
        )
        w = SshWorker(ROBOT_CONFIG.main_control_ip, ROBOT_CONFIG.main_control_user,
                       ROBOT_CONFIG.main_control_passwords)
        w.set_command(commands)
        w.command_finished.connect(lambda ec: self._on_time_fix_done(ec))
        w.error_occurred.connect(lambda e: self._on_ssh_error("FIX_TIME", e))
        self._watch_ssh_authentication(w, self._fix_time)
        self._workers.append(w)
        w.start()

    def _on_time_fix_done(self, exit_code: int):
        self.step_result.emit(HealthCheckState.FIXING_TIME, {"message": "时间已校正"})
        self._transition_to(HealthCheckState.CHECKING_IMU)
        self._check_imu()

    # ---- Step 4: IMU Check ----

    def _check_imu(self):
        w = SshWorker(ROBOT_CONFIG.main_control_ip, ROBOT_CONFIG.main_control_user,
                       ROBOT_CONFIG.main_control_passwords)
        w.set_command("bash -c 'source /opt/limx/install/setup.bash && export MROS_IP_LIST=10.192.1.x && timeout --signal=KILL 8s /opt/limx/install/bin/mrostopic hz /ImuData' 2>&1")
        w.command_finished.connect(lambda ec, worker=w: self._on_imu_output(ec, worker))
        w.error_occurred.connect(lambda e: self._on_ssh_error("IMU", e))
        self._watch_ssh_authentication(w, self._check_imu)
        self._workers.append(w)
        w.start()

    def _on_imu_output(self, exit_code: int, worker: SshWorker):
        output = worker.collected_output
        # Collect all "average rate:" values, take the last (most stable)
        matches = re.findall(r"average rate:\s*([\d.]+)", output)
        freq = float(matches[-1]) if matches else 0.0
        tolerance = ROBOT_CONFIG.imu_tolerance_hz
        passed = abs(freq - ROBOT_CONFIG.expected_imu_hz) <= tolerance

        self._result.imu_result = ImuCheckResult(
            detected_frequency=freq,
            expected_frequency=ROBOT_CONFIG.expected_imu_hz,
            passed=passed,
        )
        self.step_result.emit(HealthCheckState.CHECKING_IMU, {
            "passed": passed, "frequency_hz": freq, "expected": ROBOT_CONFIG.expected_imu_hz,
        })
        self._transition_to(HealthCheckState.COMPLETED)
        self._finish()

    # ---- Finalize ----

    def _finish(self):
        self._result.all_passed = all([
            self._result.wifi_connected,
            self._result.cpu_result.passed if self._result.cpu_result else False,
            self._result.camera_result.passed if self._result.camera_result else False,
            self._result.time_result.passed if self._result.time_result else False,
            self._result.imu_result.passed if self._result.imu_result else False,
        ])
        self._result.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.diagnostic_complete.emit(self._result)
        self._state = HealthCheckState.IDLE
        self._retry_count = 0

        # Persist result
        from database.repository import HealthCheckRepository
        HealthCheckRepository().save_result(
            "manual", self._result.all_passed, self._result.to_json())
