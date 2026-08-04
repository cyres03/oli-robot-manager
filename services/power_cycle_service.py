"""
Power cycle test orchestrator:
1. Run initial health check → baseline
2. Prompt power off → 5-min countdown → prompt power on
3. Poll WiFi → run final health check → compare
"""
from enum import Enum, auto
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from services.health_check_service import HealthCheckService
from network.wifi_manager import WifiManager
from models.health import HealthCheckResult


class PowerCycleState(Enum):
    IDLE = auto()
    RUNNING_INITIAL_CHECK = auto()
    WAITING_POWER_OFF = auto()
    COUNTDOWN = auto()
    WAITING_RECONNECT = auto()
    RUNNING_FINAL_CHECK = auto()
    COMPARING = auto()


class PowerCycleService(QObject):
    state_changed = pyqtSignal(PowerCycleState, str)
    countdown_tick = pyqtSignal(int)
    initial_check_complete = pyqtSignal(HealthCheckResult)
    final_check_complete = pyqtSignal(HealthCheckResult)
    comparison_ready = pyqtSignal(HealthCheckResult, HealthCheckResult)
    error_occurred = pyqtSignal(str)

    def __init__(self, health_service: HealthCheckService, parent=None):
        super().__init__(parent)
        self._health = health_service
        self._state = PowerCycleState.IDLE
        self._baseline: HealthCheckResult | None = None
        self._seconds_remaining = 300
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 120

        self._countdown_timer = QTimer()
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._reconnect_timer = QTimer()
        self._reconnect_timer.timeout.connect(self._poll_reconnect)

        self._health.diagnostic_complete.connect(self._on_check_complete)

    def start(self):
        self._transition_to(PowerCycleState.RUNNING_INITIAL_CHECK, "运行初始健康检查...")
        self._health.run_full_diagnostic()

    def _on_check_complete(self, result: HealthCheckResult):
        if self._state == PowerCycleState.RUNNING_INITIAL_CHECK:
            self._baseline = result
            self.initial_check_complete.emit(result)
            self._transition_to(PowerCycleState.WAITING_POWER_OFF,
                "请手动关闭机器人电源,然后点击「已关机」按钮")
        elif self._state == PowerCycleState.RUNNING_FINAL_CHECK:
            self.final_check_complete.emit(result)
            self._transition_to(PowerCycleState.COMPARING, "正在对比断电前后结果...")
            self.comparison_ready.emit(self._baseline, result)
            self._transition_to(PowerCycleState.IDLE, "断电重启测试完成")

    def confirm_power_off(self):
        if self._state != PowerCycleState.WAITING_POWER_OFF:
            return
        self._transition_to(PowerCycleState.COUNTDOWN,
            "倒计时开始,5分钟后请重新开机并连接WiFi")
        self._seconds_remaining = 300
        self._countdown_timer.start(1000)

    def _on_countdown_tick(self):
        self._seconds_remaining -= 1
        self.countdown_tick.emit(self._seconds_remaining)
        if self._seconds_remaining <= 0:
            self._countdown_timer.stop()
            self._transition_to(PowerCycleState.WAITING_RECONNECT,
                "请手动开启机器人电源并连接WiFi")
            self._reconnect_attempts = 0
            self._reconnect_timer.start(5000)

    def _poll_reconnect(self):
        self._reconnect_attempts += 1
        if WifiManager.is_robot_wifi():
            self._reconnect_timer.stop()
            self._transition_to(PowerCycleState.RUNNING_FINAL_CHECK,
                "WiFi已重连,运行最终健康检查...")
            self._health.run_full_diagnostic()
        elif self._reconnect_attempts >= self._max_reconnect_attempts:
            self._reconnect_timer.stop()
            self.error_occurred.emit("等待重连超时(10分钟),请检查机器人状态")
            self._transition_to(PowerCycleState.IDLE, "")

    def cancel(self):
        self._countdown_timer.stop()
        self._reconnect_timer.stop()
        self._transition_to(PowerCycleState.IDLE, "测试已取消")

    def _transition_to(self, state: PowerCycleState, description: str):
        self._state = state
        self.state_changed.emit(state, description)
