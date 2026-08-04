"""Main application window with sidebar, stacked content panels, terminal, and status bar."""
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter, QFrame, QMessageBox,
    QGraphicsOpacityEffect,
)
from PyQt6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QAbstractAnimation, QTimer, QEvent
from time import monotonic
from services.dance_service import DanceService
from services.health_check_service import HealthCheckService
from services.power_cycle_service import PowerCycleService
from services.connection_service import ConnectionService
from services.calibrate_service import CalibrateService
from services.robot_monitor import RobotMonitor
from workers.mcp_worker import McpWorker
from ui.widgets.sidebar import Sidebar
from ui.widgets.status_bar_widget import StatusBarWidget
from ui.widgets.status_banner import StatusBanner
from ui.widgets.terminal_panel import TerminalPanel
from ui.panels.dance_library_panel import DanceLibraryPanel
from ui.panels.health_check_panel import HealthCheckPanel
from ui.panels.settings_panel import SettingsPanel
from ui.panels.calibrate_panel import CalibratePanel
from ui.panels.control_panel import ControlPanel
from ui.panels.acceptance_test_panel import AcceptanceTestPanel
from ui.dialogs.ssh_terminal_window import open_native_ssh_terminal
from config import ROBOT_CONFIG


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Oli Robot Manager")
        self.resize(1280, 860)
        self.setMinimumSize(1024, 680)

        self._dance_service: DanceService | None = None
        self._health_service: HealthCheckService | None = None
        self._power_cycle_service: PowerCycleService | None = None
        self._connection_service: ConnectionService | None = None
        self._calibrate_service: CalibrateService | None = None
        self._mcp_worker: McpWorker | None = None
        self._robot_monitor: RobotMonitor | None = None
        self._stack_effect: QGraphicsOpacityEffect | None = None
        self._stack_fade_animation: QPropertyAnimation | None = None
        self._robot_identity_timer: QTimer | None = None
        self._last_status_log_key: tuple[str, str] | None = None
        self._last_status_log_at = 0.0
        self._was_minimized = False
        self._ui_log_path = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "OliRobotManager",
            "ui_events.log",
        )

        self._build_ui()
        self._log_ui_event("window_init")

    def set_services(
        self,
        dance_service: DanceService,
        health_service: HealthCheckService,
        power_cycle_service: PowerCycleService,
        connection_service: ConnectionService,
        calibrate_service: CalibrateService = None,
        mcp_worker: McpWorker = None,
        robot_monitor: RobotMonitor = None,
    ):
        self._dance_service = dance_service
        self._health_service = health_service
        self._power_cycle_service = power_cycle_service
        self._connection_service = connection_service
        self._calibrate_service = calibrate_service
        self._mcp_worker = mcp_worker
        self._robot_monitor = robot_monitor

        self._build_content()
        self._wire_signals()
        self._connection_service.check_wifi()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.sidebar = Sidebar()
        self.stack = None
        self.terminal = TerminalPanel()
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(self._v_splitter)

    def _build_content(self):
        from PyQt6.QtWidgets import QStackedWidget

        while self._v_splitter.count() > 0:
            self._v_splitter.widget(0).setParent(None)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.addWidget(self.sidebar)

        # Content area: status banner + stack
        content_wrapper = QWidget()
        content_layout = QVBoxLayout(content_wrapper)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.status_banner = StatusBanner()
        content_layout.addWidget(self.status_banner)

        self.stack = QStackedWidget()
        self.dance_panel = DanceLibraryPanel(self._dance_service)
        self.control_panel = ControlPanel(self._mcp_worker)
        self.acceptance_panel = AcceptanceTestPanel(self._power_cycle_service)
        self.health_panel = HealthCheckPanel(self._health_service)
        self.calibrate_panel = CalibratePanel(self._calibrate_service)
        self.settings_panel = SettingsPanel()

        self.stack.addWidget(self.dance_panel)      # 0: 舞蹈&动作库
        self.stack.addWidget(self.control_panel)     # 1: 基础控制
        self.stack.addWidget(self.acceptance_panel)  # 2: 验收测试
        self.stack.addWidget(self.health_panel)      # 3: 健康检查
        self.stack.addWidget(self.calibrate_panel)   # 4: 校零
        self.stack.addWidget(self.settings_panel)    # 5: 设置
        self.stack.setCurrentIndex(0)
        self._setup_stack_animation()

        content_layout.addWidget(self.stack)
        h_splitter.addWidget(content_wrapper)
        h_splitter.setSizes([180, 1100])
        h_splitter.setStretchFactor(1, 1)
        self._v_splitter.addWidget(h_splitter)

        term_frame = QFrame()
        term_layout = QVBoxLayout(term_frame)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.addWidget(self.terminal)
        self._v_splitter.addWidget(term_frame)
        self._v_splitter.setSizes([720, 120])
        self._v_splitter.setStretchFactor(0, 1)

        self.status_bar_widget = StatusBarWidget(self._connection_service)
        self.setStatusBar(self.status_bar_widget)

    def _wire_signals(self):
        self.sidebar.navigation_requested.connect(self._on_navigate)

        # Health check → terminal
        self._health_service.step_started.connect(
            lambda s, d: self.terminal.append_log(f"[诊断] {d}", "info"))
        self._health_service.step_result.connect(
            lambda s, r: self.terminal.append_log(
                f"[诊断] {s.name}: {'PASS' if r.get('passed') else 'FAIL'}",
                "pass" if r.get('passed') else "error"))
        self._health_service.diagnostic_error.connect(
            lambda m: self.terminal.append_log(f"[诊断错误] {m}", "error"))

        # Dance/Motion → terminal
        self._dance_service.dance_executed.connect(
            lambda n, c: self.terminal.append_log(f"[舞蹈] '{n}' (第{c}次)", "info"))
        self._dance_service.motion_executed.connect(
            lambda n, c: self.terminal.append_log(f"[动作] '{n}' (第{c}次)", "info"))
        self._dance_service.sequence_finished.connect(
            lambda n: self.terminal.append_log(f"[序列] '{n}' 完成", "pass"))
        self._dance_service.error_occurred.connect(
            lambda m: self.terminal.append_log(f"[错误] {m}", "error"))
        self._dance_service.action_state_changed.connect(self._on_dance_action_state_changed)

        # Power cycle → terminal
        self._power_cycle_service.state_changed.connect(
            lambda s, d: self.terminal.append_log(f"[断电测试] {d}", "warn"))
        self._power_cycle_service.error_occurred.connect(
            lambda m: self.terminal.append_log(f"[断电错误] {m}", "error"))

        # Calibrate → terminal
        self._calibrate_service.calibrate_started.connect(
            lambda t: self.terminal.append_log(f"[校零] {t} 开始...", "info"))
        self._calibrate_service.calibrate_result.connect(
            lambda t, ok, d: self.terminal.append_log(
                f"[校零] {t}: {'成功' if ok else '失败'} {d}", "pass" if ok else "error"))
        self._calibrate_service.backlash_launched.connect(
            lambda p: self.terminal.append_log(f"[校零] 启动: {p}", "info"))

        # Robot monitor → status banner
        self._robot_monitor.status_updated.connect(self.status_banner.update_status)
        self._robot_monitor.status_updated.connect(self.control_panel.update_robot_status)
        self._robot_monitor.connected.connect(
            lambda ok: self.status_banner.set_disconnected() if not ok else None)
        self._robot_monitor.status_updated.connect(self._on_robot_status)

        # Control panel actions
        self.control_panel.action_requested.connect(self._on_control_action)
        self.calibrate_panel.calibrate_requested.connect(self._on_calibrate_request)
        self.acceptance_panel.log_message.connect(
            lambda message, level: self.terminal.append_log(message, level))

        self._robot_identity_timer = QTimer(self)
        self._robot_identity_timer.setInterval(5000)
        self._robot_identity_timer.timeout.connect(self._poll_robot_identity)
        self._robot_identity_timer.start()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return

        was_minimized = self._was_minimized
        self._was_minimized = self.isMinimized()
        self._log_ui_event("window_state_change", was_minimized=was_minimized)
        if was_minimized and not self._was_minimized:
            self._restore_from_minimized()

    def _on_dance_action_state_changed(self, running: bool, label: str):
        self._log_ui_event("dance_action_state", running=running, label=label)

    def _restore_from_minimized(self):
        self._log_ui_event("restore_from_minimized")
        if self._stack_fade_animation and self._stack_fade_animation.state() == QAbstractAnimation.State.Running:
            self._stack_fade_animation.stop()
        if self._stack_effect:
            self._stack_effect.setOpacity(1.0)
        QTimer.singleShot(0, self._activate_after_restore)

    def _activate_after_restore(self):
        if self.isMinimized():
            self._log_ui_event("activate_after_restore_skipped_minimized")
            return
        if self.centralWidget():
            self.centralWidget().updateGeometry()
            self.centralWidget().update()
        self.raise_()
        self.activateWindow()
        self._log_ui_event("activate_after_restore_done")

    def _log_ui_event(self, event_name: str, **fields):
        try:
            os.makedirs(os.path.dirname(self._ui_log_path), exist_ok=True)
            if os.path.exists(self._ui_log_path) and os.path.getsize(self._ui_log_path) > 1024 * 1024:
                os.replace(self._ui_log_path, self._ui_log_path + ".1")
            animation_state = "none"
            if self._stack_fade_animation:
                animation_state = self._stack_fade_animation.state().name
            base_fields = {
                "event": event_name,
                "minimized": self.isMinimized(),
                "active": self.isActiveWindow(),
                "visible": self.isVisible(),
                "window_state": str(self.windowState()),
                "stack_index": self.stack.currentIndex() if self.stack else None,
                "animation_state": animation_state,
            }
            base_fields.update(fields)
            detail = " ".join(f"{key}={value!r}" for key, value in base_fields.items())
            with open(self._ui_log_path, "a", encoding="utf-8") as file:
                file.write(f"{datetime.now().isoformat(timespec='milliseconds')} {detail}\n")
        except Exception:
            pass

    def _on_navigate(self, key: str):
        if key.startswith("ssh:"):
            _, target = key.split(":", 1)
            user, host = target.rsplit("@", 1)
            self.terminal.append_log(f"[SSH] 打开终端 {user}@{host}...", "command")
            open_native_ssh_terminal(host, user)
            return

        if key == "wifi_selector":
            self._show_wifi_selector()
            return

        index_map = {
            "dance_library": 0,
            "controls": 1,
            "acceptance": 2,
            "health_check": 3,
            "power_cycle": 2,
            "calibrate": 4,
            "settings": 5,
        }
        idx = index_map.get(key, 0)
        if self.stack:
            self._switch_page(idx)
        self.status_bar_widget.setVisible(True)

    def _setup_stack_animation(self):
        self._stack_effect = QGraphicsOpacityEffect(self.stack)
        self._stack_effect.setOpacity(1.0)
        self.stack.setGraphicsEffect(self._stack_effect)

        self._stack_fade_animation = QPropertyAnimation(self._stack_effect, b"opacity", self)
        self._stack_fade_animation.setDuration(180)
        self._stack_fade_animation.setStartValue(0.82)
        self._stack_fade_animation.setEndValue(1.0)
        self._stack_fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _switch_page(self, index: int):
        current_index = self.stack.currentIndex()
        if index == current_index:
            return
        if current_index == 0 and hasattr(self, "dance_panel"):
            self.dance_panel.stop_continuous_walk(reset_sliders=True, send_stop=True)

        self.stack.setCurrentIndex(index)
        if not self._stack_effect or not self._stack_fade_animation:
            return

        if self._stack_fade_animation.state() == QAbstractAnimation.State.Running:
            self._stack_fade_animation.stop()
        self._stack_effect.setOpacity(0.82)
        self._stack_fade_animation.start()

    def _on_control_action(self, tool_name: str, arguments: dict):
        if hasattr(self, "dance_panel"):
            self.dance_panel.stop_continuous_walk(reset_sliders=True, send_stop=True)
        self.terminal.append_log(f"[控制] {tool_name}", "command")
        self._mcp_worker.call_tool(tool_name, arguments)

    def _on_robot_status(self, info: dict):
        """Log status changes to terminal."""
        status = info.get("robot_status", "")
        battery = info.get("battery", "")
        log_key = (status, battery)
        now = monotonic()
        if status and status != "?" and (log_key != self._last_status_log_key or now - self._last_status_log_at > 30):
            self._last_status_log_key = log_key
            self._last_status_log_at = now
            self.terminal.append_log(f"[状态] {status} | 电量 {battery}", "info")

    def _on_calibrate_request(self, cal_type: str):
        if cal_type == "mission_engine":
            self._calibrate_service.run_mission_engine_calibrate()
        elif cal_type == "websocket":
            self._calibrate_service.run_websocket_calibrate()
        elif cal_type == "backlash":
            self._calibrate_service.launch_backlash_test()

    def _show_wifi_selector(self):
        from ui.dialogs.wifi_selector_dialog import WifiSelectorDialog
        dialog = WifiSelectorDialog(self)
        dialog.network_selected.connect(
            lambda ssid: self.terminal.append_log(f"[WiFi] 已选择连接: {ssid}", "pass"))
        if dialog.exec() == WifiSelectorDialog.DialogCode.Accepted:
            self.sidebar.refresh_wifi_status()
            self._connection_service.check_wifi()
            self.terminal.append_log("[WiFi] 正在等待新机器人网络就绪...", "info")
            self._refresh_robot_after_wifi_change(0)

    def _refresh_robot_after_wifi_change(self, attempt: int):
        QTimer.singleShot(1200 if attempt == 0 else 2000, lambda: self._apply_robot_after_wifi_change(attempt))

    def _apply_robot_after_wifi_change(self, attempt: int):
        from config import detect_accid_from_robot_portal, extract_robot_accid
        from network.wifi_manager import WifiManager

        self.sidebar.refresh_wifi_status()
        self._connection_service.check_wifi()

        ssid = WifiManager.get_robot_ssid()
        ssid_accid = extract_robot_accid(ssid) if ssid else None
        portal_accid = detect_accid_from_robot_portal(timeout=2.0)
        new_accid = portal_accid or ssid_accid

        if not new_accid:
            if attempt < 5:
                self.terminal.append_log(f"[WiFi] 新机器人信息未就绪，重试 {attempt + 1}/5...", "warn")
                self._refresh_robot_after_wifi_change(attempt + 1)
            else:
                self.terminal.append_log("[WiFi] 未能识别新机器人 ACCID，请确认 8080 页面可访问或在设置中手动填写。", "error")
            return

        self._set_control_target(new_accid, "已切换控制目标")
        self._dance_service.load_dances()
        self._dance_service.load_motions()

    def _poll_robot_identity(self):
        from config import detect_accid_from_robot_portal, extract_robot_accid
        from network.wifi_manager import WifiManager

        ssid = WifiManager.get_robot_ssid()
        ssid_accid = extract_robot_accid(ssid) if ssid else None
        portal_accid = detect_accid_from_robot_portal(timeout=0.35) if ssid_accid else None
        new_accid = portal_accid or ssid_accid
        if new_accid and new_accid != ROBOT_CONFIG.ws_accid:
            self._set_control_target(new_accid, "检测到机器人网络变化，已切换控制目标")
            self._dance_service.load_dances()
            self._dance_service.load_motions()

    def _set_control_target(self, accid: str, message: str):
        ROBOT_CONFIG.ws_accid = accid
        if self._mcp_worker:
            self._mcp_worker.update_accid(accid)
        if hasattr(self, "settings_panel"):
            field = self.settings_panel._fields.get("ws_accid")
            if field:
                field.setText(accid)
        self.terminal.append_log(f"[系统] {message}: {accid}", "pass")
