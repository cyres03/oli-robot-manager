"""Main application window with sidebar, stacked content panels, terminal, and status bar."""
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter, QFrame,
    QGraphicsOpacityEffect,
)
from PyQt6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QAbstractAnimation, QTimer, QEvent
from time import monotonic
from services.dance_service import DanceService
from services.health_check_service import HealthCheckService
from services.power_cycle_service import PowerCycleService
from services.connection_service import ConnectionService
from services.calibrate_service import CalibrateService
from services.managed_test_service import TestCaseService
from services import credential_store
from services.robot_monitor import RobotMonitor
from network.ssh_client import current_robot_id
from workers.mcp_worker import McpWorker
from workers.ssh_key_install_worker import SshKeyInstallWorker
from workers.ssh_worker import SshWorker
from models.robot_profile import RobotIdentity, RobotIdentityStatus
from models.workspace import CONNECTION_WORKSPACE, resolve_workspace
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
from ui.panels.managed_test_panel import TestCasePanel
from ui.panels.mros_node_health_panel import MrosNodeHealthPanel
from ui.dialogs.ssh_terminal_window import open_native_ssh_terminal
from ui.dialogs.password_dialog import PasswordDialog
from ui.dialogs.message_dialog import AppMessageBox
from config import ROBOT_CONFIG


def ssh_authorization_error_title(error_code: str) -> str:
    return {
        "authentication": "SSH 密码验证失败",
        "key_write": "SSH 公钥写入失败",
        "key_rejected": "SSH 公钥被拒绝",
        "key_connection": "SSH 密钥复验未完成",
        "robot_mismatch": "机器人连接已切换",
        "connection": "SSH 连接失败",
    }.get(error_code, "SSH 密钥授权失败")


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
        self._test_case_service: TestCaseService | None = None
        self._stack_effect: QGraphicsOpacityEffect | None = None
        self._stack_fade_animation: QPropertyAnimation | None = None
        self._robot_identity_timer: QTimer | None = None
        self._connection_timer: QTimer | None = None
        self._ssh_key_worker: SshKeyInstallWorker | None = None
        self._ssh_probe_worker: SshWorker | None = None
        self._last_status_log_key: tuple[str, str] | None = None
        self._last_status_log_at = 0.0
        self._last_identity_status_key: tuple[str, str] | None = None
        self._active_workspace_key = "connection"
        self._active_workspace = CONNECTION_WORKSPACE
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
        test_case_service: TestCaseService = None,
    ):
        self._dance_service = dance_service
        self._health_service = health_service
        self._power_cycle_service = power_cycle_service
        self._connection_service = connection_service
        self._calibrate_service = calibrate_service
        self._mcp_worker = mcp_worker
        self._robot_monitor = robot_monitor
        self._test_case_service = test_case_service

        self._build_content()
        self._wire_signals()
        self._refresh_connection_status()

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
        self.test_case_panel = TestCasePanel(self._test_case_service)
        self.mros_node_health_panel = MrosNodeHealthPanel(self._test_case_service)

        self.stack.addWidget(self.dance_panel)      # 0: 舞蹈&动作库
        self.stack.addWidget(self.control_panel)     # 1: 基础控制
        self.stack.addWidget(self.acceptance_panel)  # 2: 验收测试
        self.stack.addWidget(self.health_panel)      # 3: 健康检查
        self.stack.addWidget(self.calibrate_panel)   # 4: 校零
        self.stack.addWidget(self.settings_panel)    # 5: 设置
        self.stack.addWidget(self.test_case_panel)   # 6: 测试用例
        self.stack.addWidget(self.mros_node_health_panel)  # 7: Luna mROS 节点健康
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
        self._health_service.ssh_authorization_required.connect(
            self._authorize_ssh_for_health_check
        )

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
        self._calibrate_service.ssh_authorization_required.connect(
            self._authorize_ssh_for_calibration
        )

        # Robot monitor → status banner
        self._robot_monitor.status_updated.connect(self.status_banner.update_status)
        self._robot_monitor.status_updated.connect(self.control_panel.update_robot_status)
        self._robot_monitor.connected.connect(
            lambda ok: self.status_banner.set_disconnected() if not ok else None)
        self._robot_monitor.connected.connect(self._connection_service.update_ws)
        self._robot_monitor.status_updated.connect(
            lambda _info: self._connection_service.update_ws(True))
        self._robot_monitor.status_updated.connect(self._on_robot_status)
        self.acceptance_panel.ssh_connection_changed.connect(self._connection_service.update_ssh)
        self.acceptance_panel.ssh_authorization_required.connect(
            self._authorize_ssh_for_acceptance
        )
        self.acceptance_panel.sudo_password_required.connect(
            self._request_perception_sudo_password
        )
        self.settings_panel.credentials_clear_requested.connect(
            self._clear_current_robot_credentials
        )
        self._test_case_service.ssh_authorization_required.connect(
            self._authorize_ssh_for_test_case
        )
        self._test_case_service.error_occurred.connect(
            lambda message: self.terminal.append_log(
                f"[测试用例] {message}", "error"
            )
        )

        # Control panel actions
        self.control_panel.action_requested.connect(self._on_control_action)
        self.calibrate_panel.calibrate_requested.connect(self._on_calibrate_request)
        self.acceptance_panel.log_message.connect(
            lambda message, level: self.terminal.append_log(message, level))

        self._robot_identity_timer = QTimer(self)
        self._robot_identity_timer.setInterval(5000)
        self._robot_identity_timer.timeout.connect(self._poll_robot_identity)
        self._robot_identity_timer.start()

        self._connection_timer = QTimer(self)
        self._connection_timer.setInterval(5000)
        self._connection_timer.timeout.connect(self._refresh_connection_status)
        self._connection_timer.start()

    def _refresh_connection_status(self):
        self._connection_service.check_wifi()
        if ROBOT_CONFIG.mcp_supported and ROBOT_CONFIG.mcp_url:
            self._connection_service.check_mcp(ROBOT_CONFIG.mcp_url)
        else:
            self._connection_service.update_mcp(None)

    def apply_robot_identity(
        self,
        identity: RobotIdentity,
        message: str = "",
        initial: bool = False,
    ):
        previous_accid = ROBOT_CONFIG.ws_accid
        ready = ROBOT_CONFIG.apply_identity(identity)
        profile = ROBOT_CONFIG.active_profile
        workspace = resolve_workspace(profile)
        allowed_tools = profile.allowed_tools if profile else frozenset()

        self._connection_service.update_ssh(False)
        if self._mcp_worker:
            self._mcp_worker.update_target(
                ROBOT_CONFIG.ws_accid if ready else None,
                allowed_tools,
                ROBOT_CONFIG.websocket_url,
                profile.key if profile else "",
            )
        self.sidebar.apply_profile(profile)
        self.sidebar.apply_workspace(workspace)
        self._active_workspace = workspace
        if workspace.key != self._active_workspace_key:
            self._active_workspace_key = workspace.key
            self._on_navigate(workspace.default_route)
        if hasattr(self, "acceptance_panel"):
            self.acceptance_panel.apply_profile(profile)
        if hasattr(self, "control_panel"):
            self.control_panel.apply_profile(profile)
        if hasattr(self, "dance_panel"):
            self.dance_panel.apply_profile(profile)
        if hasattr(self, "calibrate_panel"):
            self.calibrate_panel.apply_profile(profile)
        if hasattr(self, "settings_panel"):
            self.settings_panel.apply_profile(profile)
        if self._test_case_service:
            self._test_case_service.apply_context(
                profile,
                ROBOT_CONFIG.ws_accid if ready else "",
                ROBOT_CONFIG.firmware_version,
            )

        if ready and profile and identity.accid:
            self._dance_service.switch_resource_context(
                profile.key,
                identity.accid,
                ROBOT_CONFIG.firmware_version,
            )
            self.status_banner.set_identity(profile.display_name, identity.accid)
            if hasattr(self, "settings_panel"):
                self.settings_panel.refresh_credential_status()
            status_message = message or identity.message
            if status_message and (initial or previous_accid != identity.accid):
                self.terminal.append_log(f"[系统] {status_message}", "pass")
            if initial or previous_accid != identity.accid:
                self._dance_service.load_dances()
                self._dance_service.load_motions()
            return

        self._dance_service.switch_resource_context("", "", "")
        error_message = identity.message or "机器人身份未识别"
        self.status_banner.set_identity_error(error_message)
        status_key = (identity.status.value, error_message)
        if status_key != self._last_identity_status_key:
            self._last_identity_status_key = status_key
            self.terminal.append_log(f"[系统] {error_message}，控制功能已锁定", "error")
        if initial:
            QTimer.singleShot(0, self._show_wifi_selector)

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
            self._open_ssh_terminal(host, user)
            return

        if key == "wifi_selector":
            self._show_wifi_selector()
            return

        if self._active_workspace.route(key) is None:
            self.terminal.append_log(
                f"[工作区] 当前 {self._active_workspace.display_name} 不允许打开 {key}",
                "warn",
            )
            return

        index_map = {
            "dance_library": 0,
            "controls": 1,
            "test_cases": 6,
            "acceptance": 2,
            "log_analysis": 2,
            "health_check": 7 if self._active_workspace.key == "luna" else 3,
            "power_cycle": 2,
            "calibrate": 4,
            "settings": 5,
        }
        idx = index_map.get(key, 0)
        if self.stack:
            self._switch_page(idx)
            if key == "log_analysis":
                self.acceptance_panel.tabs.setCurrentWidget(
                    self.acceptance_panel.log_analyzer
                )
            elif key == "acceptance":
                self.acceptance_panel.tabs.setCurrentWidget(
                    self.acceptance_panel.auto_tab
                )
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
        firmware_version = str(info.get("version", "")).strip()
        if (
            firmware_version
            and firmware_version != "?"
            and firmware_version != ROBOT_CONFIG.firmware_version
            and ROBOT_CONFIG.active_profile
            and ROBOT_CONFIG.ws_accid
        ):
            ROBOT_CONFIG.firmware_version = firmware_version
            self._dance_service.switch_resource_context(
                ROBOT_CONFIG.profile_key,
                ROBOT_CONFIG.ws_accid,
                firmware_version,
            )
            self._dance_service.load_dances()
            self._dance_service.load_motions()
            if self._test_case_service:
                self._test_case_service.apply_context(
                    ROBOT_CONFIG.active_profile,
                    ROBOT_CONFIG.ws_accid,
                    firmware_version,
                )
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

    def _authorize_ssh_for_calibration(
        self, host: str, username: str, operation: str, robot_id: str
    ):
        self._request_ssh_key_authorization(
            host,
            username,
            robot_id,
            lambda: self._calibrate_service.retry_after_ssh_authorization(operation),
            lambda detail: self._calibrate_service.cancel_ssh_authorization(
                operation, detail
            ),
        )

    def _authorize_ssh_for_acceptance(
        self, host: str, username: str, robot_id: str
    ):
        self._request_ssh_key_authorization(
            host,
            username,
            robot_id,
            lambda: self.acceptance_panel.finish_ssh_authorization(True, ""),
            lambda detail: self.acceptance_panel.finish_ssh_authorization(
                False, detail
            ),
        )

    def _authorize_ssh_for_health_check(
        self, host: str, username: str, robot_id: str
    ):
        self._request_ssh_key_authorization(
            host,
            username,
            robot_id,
            lambda: self._health_service.finish_ssh_authorization(True, ""),
            lambda detail: self._health_service.finish_ssh_authorization(
                False, detail
            ),
        )

    def _authorize_ssh_for_test_case(
        self,
        host: str,
        username: str,
        robot_id: str,
        case_id: str,
    ):
        self._request_ssh_key_authorization(
            host,
            username,
            robot_id,
            lambda: self._test_case_service.retry_after_authorization(case_id),
            lambda detail: self._test_case_service.cancel_authorization(
                case_id, detail
            ),
        )

    def _request_perception_sudo_password(
        self, host: str, username: str, robot_id: str
    ):
        if not robot_id or robot_id != ROBOT_CONFIG.ws_accid:
            self.acceptance_panel.submit_sudo_password("")
            return

        password = credential_store.get_password(robot_id, host, username)
        if password:
            self.acceptance_panel.submit_sudo_password(
                password,
                remember=True,
                from_store=True,
            )
            password = ""
            return

        if ROBOT_CONFIG.perception_password:
            password = ROBOT_CONFIG.perception_password
            self.acceptance_panel.submit_sudo_password(
                password,
                remember=False,
                from_store=False,
            )
            password = ""
            return

        password, remember, accepted = PasswordDialog.get_password(
            self,
            "感知机时间校准",
            f"机器人 {robot_id} 的感知机时间不正确。\n"
            f"请输入 {username}@{host} 的 sudo 密码：\n"
            "可安全保存到系统凭据管理器，供该机器人后续自动校时使用。",
        )
        self.acceptance_panel.submit_sudo_password(
            password if accepted else "",
            remember=remember,
            from_store=False,
        )
        password = ""

    def _open_ssh_terminal(self, host: str, username: str):
        if self._ssh_probe_worker and self._ssh_probe_worker.isRunning():
            self.terminal.append_log("[SSH] 正在检查当前机器人 SSH...", "warn")
            return

        robot_id = ROBOT_CONFIG.ws_accid
        connected_robot_id = current_robot_id()
        if not robot_id or connected_robot_id != robot_id:
            self.terminal.append_log(
                "[SSH] 机器人身份尚未就绪或正在切换，请稍后重试", "error"
            )
            return
        self.terminal.append_log(
            f"[SSH] 正在检查 {username}@{host} 的密钥授权...", "info"
        )
        self._ssh_probe_worker = SshWorker(
            host, username, [], self, robot_id=robot_id
        )
        self._ssh_probe_worker.set_command("true")
        self._ssh_probe_worker.command_finished.connect(
            lambda _exit_code: self._open_verified_ssh_terminal(
                host, username, robot_id
            )
        )
        self._ssh_probe_worker.authentication_required.connect(
            lambda auth_host, auth_username, expected_robot_id:
            self._request_ssh_key_authorization(
                auth_host,
                auth_username,
                expected_robot_id,
                lambda: self._open_verified_ssh_terminal(
                    host, username, robot_id
                ),
                lambda detail: self.terminal.append_log(f"[SSH] {detail}", "error"),
            )
        )
        self._ssh_probe_worker.error_occurred.connect(
            lambda detail: self.terminal.append_log(f"[SSH] {detail}", "error")
        )
        self._ssh_probe_worker.start()

    def _open_verified_ssh_terminal(
        self, host: str, username: str, robot_id: str
    ):
        connected_robot_id = current_robot_id()
        if ROBOT_CONFIG.ws_accid != robot_id or connected_robot_id != robot_id:
            self.terminal.append_log(
                f"[SSH] 机器人已从 {robot_id} 切换，未打开旧目标终端",
                "error",
            )
            return
        open_native_ssh_terminal(host, username, robot_id)

    def _request_ssh_key_authorization(
        self, host: str, username: str, robot_id: str, on_success, on_failure
    ):
        if self._ssh_key_worker and self._ssh_key_worker.isRunning():
            on_failure("另一个 SSH 密钥授权正在进行中")
            return

        if not robot_id or robot_id != ROBOT_CONFIG.ws_accid:
            on_failure("机器人已切换或身份尚未识别，已取消旧操作")
            return

        password = credential_store.get_password(robot_id, host, username)
        if password:
            self._start_ssh_key_authorization(
                host,
                username,
                robot_id,
                password,
                remember=True,
                from_store=True,
                on_success=on_success,
                on_failure=on_failure,
            )
            password = ""
            return

        self._prompt_ssh_key_authorization(
            host, username, robot_id, on_success, on_failure
        )

    def _prompt_ssh_key_authorization(
        self, host: str, username: str, robot_id: str, on_success, on_failure
    ):
        password, remember, accepted = PasswordDialog.get_password(
            self,
            "首次连接机器人",
            f"机器人 {robot_id} 尚未授权本机 SSH 密钥。\n"
            f"请输入 {username}@{host} 的密码：\n"
            "授权成功后将优先使用 SSH 密钥；也可将密码安全保存到系统凭据管理器。",
        )
        if not accepted or not password:
            self.terminal.append_log("[SSH] 已取消当前机器人的密钥授权", "warn")
            on_failure("已取消 SSH 密钥授权")
            return

        self._start_ssh_key_authorization(
            host,
            username,
            robot_id,
            password,
            remember,
            False,
            on_success,
            on_failure,
        )
        password = ""

    def _start_ssh_key_authorization(
        self,
        host: str,
        username: str,
        robot_id: str,
        password: str,
        remember: bool,
        from_store: bool,
        on_success,
        on_failure,
    ):
        self.terminal.append_log(f"[SSH] 正在为 {username}@{host} 授权本机密钥...", "info")
        self._ssh_key_worker = SshKeyInstallWorker(
            host,
            username,
            password,
            robot_id,
            remember=remember,
            from_store=from_store,
            parent=self,
        )
        password = ""
        self._ssh_key_worker.completed.connect(
            lambda success, detail, error_code: self._on_ssh_key_authorized(
                success,
                detail,
                error_code,
                host,
                username,
                robot_id,
                on_success,
                on_failure,
            )
        )
        self._ssh_key_worker.start()

    def _on_ssh_key_authorized(
        self,
        success: bool,
        detail: str,
        error_code: str,
        host: str,
        username: str,
        robot_id: str,
        on_success,
        on_failure,
    ):
        worker = self._ssh_key_worker
        remember = worker.remember if worker else False
        from_store = worker.from_store if worker else False
        credential_saved = worker.credential_saved if worker else None
        if success and robot_id != ROBOT_CONFIG.ws_accid:
            success = False
            detail = "授权期间机器人已切换，请在当前机器人上重试"
        self.terminal.append_log(f"[SSH] {detail}", "pass" if success else "error")
        if success:
            if remember and not from_store:
                self.terminal.append_log(
                    "[凭据] 密码已保存到系统凭据管理器"
                    if credential_saved
                    else "[凭据] 系统凭据管理器不可用，密码未保存",
                    "pass" if credential_saved else "warn",
                )
            self._connection_service.update_ssh(True)
            on_success()
            return
        if from_store and error_code == "authentication":
            credential_store.delete_password(robot_id, host, username)
            self.terminal.append_log(
                "[凭据] 已保存密码失效，已从系统凭据管理器删除",
                "warn",
            )
            self._prompt_ssh_key_authorization(
                host, username, robot_id, on_success, on_failure
            )
            return
        on_failure(detail)
        dialog_title = ssh_authorization_error_title(error_code)
        AppMessageBox.warning(self, dialog_title, detail)

    def _clear_current_robot_credentials(self):
        robot_id = ROBOT_CONFIG.ws_accid
        cleared = credential_store.clear_robot_passwords(
            robot_id,
            [
                (ROBOT_CONFIG.main_control_ip, ROBOT_CONFIG.main_control_user),
                (ROBOT_CONFIG.perception_ip, ROBOT_CONFIG.perception_user),
            ],
        )
        self.settings_panel.refresh_credential_status()
        AppMessageBox.information(
            self,
            "清除已保存密码",
            f"已清除机器人 {robot_id} 的 {cleared} 个系统凭据。",
        )

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
        from config import detect_robot_identity

        self.sidebar.refresh_wifi_status()
        self._connection_service.check_wifi()

        identity = detect_robot_identity(timeout=2.0)
        if not identity.ready:
            if identity.status != RobotIdentityStatus.NO_TARGET:
                self.apply_robot_identity(identity)
            elif attempt < 5:
                self.terminal.append_log(f"[WiFi] 新机器人信息未就绪，重试 {attempt + 1}/5...", "warn")
                self._refresh_robot_after_wifi_change(attempt + 1)
            else:
                self.apply_robot_identity(identity)
            return

        self.apply_robot_identity(identity, "已切换控制目标")

    def _poll_robot_identity(self):
        from config import detect_robot_identity

        identity = detect_robot_identity(timeout=0.35)
        current_profile_key = ROBOT_CONFIG.profile_key
        next_profile_key = identity.profile.key if identity.profile else ""
        if (
            identity.accid != (ROBOT_CONFIG.ws_accid or None)
            or next_profile_key != current_profile_key
        ):
            self.apply_robot_identity(
                identity,
                "检测到机器人网络变化，已切换控制目标" if identity.ready else "",
            )
