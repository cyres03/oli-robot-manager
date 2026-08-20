"""Calibration panel with MissionEngine calibrate and backlash test launcher."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGroupBox, QTextEdit,
    QGridLayout, QLineEdit,
)
from PyQt6.QtCore import pyqtSignal
from config import ROBOT_CONFIG
from services.calibrate_service import CalibrateService


class CalibratePanel(QWidget):
    calibrate_requested = pyqtSignal(str)  # "mission_engine" or "backlash"

    def __init__(self, calibrate_service: CalibrateService, parent=None):
        super().__init__(parent)
        self._service = calibrate_service
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        title = QLabel("校零 (Calibration)")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #1D2129; border: none; background: transparent;")
        layout.addWidget(title)

        desc = QLabel("两种方式：完整校零 / 外部 backlash 测试工具")
        desc.setStyleSheet("color: #86909C; font-size: 13px; border: none; background: transparent;")
        layout.addWidget(desc)

        group_style = (
            "QGroupBox { color: #1D2129; font-size: 14px; font-weight: 700; "
            "border: 1px solid #E5E6EB; border-radius: 10px; margin-top: 12px; "
            "padding-top: 24px; background: #FFFFFF; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }"
        )

        # ---- MissionEngine calibrate ----
        ws_group = QGroupBox("完整校零 (MissionEngine)")
        ws_group.setStyleSheet(group_style)
        ws_layout = QVBoxLayout(ws_group)

        ws_info = QLabel("通过 SSH 调用 mission_engine/switch_state，走遥控器 L1+R1 相同的完整校零链路。\n"
                  "触发前会关闭 SDK LED 控制，避免白灯覆盖机器人默认蓝色校零灯语。")
        ws_info.setStyleSheet("color: #4E5969; font-size: 12px; background: transparent;")
        ws_layout.addWidget(ws_info)

        ws_btn_row = QHBoxLayout()
        self.ws_calibrate_btn = QPushButton("执行完整校零")
        self.ws_calibrate_btn.setStyleSheet(
            "QPushButton { background: #6C5CE7; color: #fff; padding: 10px 20px; "
            "border-radius: 6px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #5A4BD1; }"
            "QPushButton:disabled { background: #C9CDD4; color: #FFF; }")
        self.ws_calibrate_btn.clicked.connect(lambda: self.calibrate_requested.emit("mission_engine"))
        ws_btn_row.addWidget(self.ws_calibrate_btn)
        ws_btn_row.addStretch()
        ws_layout.addLayout(ws_btn_row)

        layout.addWidget(ws_group)

        # ---- Backlash test ----
        bl_group = QGroupBox("Backlash 测试 (backlash-console-v0.5.exe)")
        bl_group.setStyleSheet(group_style)
        bl_layout = QVBoxLayout(bl_group)

        bl_info = QLabel("直接通过 SSH 在机器人端执行 backlash_detection，自动上传检测程序并下载 YAML 结果。\n"
                  "不再打开外部 Web 控制台，也不需要重复输入连接参数。")
        bl_info.setStyleSheet("color: #4E5969; font-size: 12px; background: transparent;")
        bl_layout.addWidget(bl_info)

        self.bl_state_label = QLabel("状态: 未连接")
        self.bl_state_label.setStyleSheet("color: #86909C; font-size: 12px; background: transparent;")
        bl_layout.addWidget(self.bl_state_label)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        input_style = "QLineEdit { background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; padding: 7px 10px; }"
        self.bl_host_input = QLineEdit(ROBOT_CONFIG.main_control_ip)
        self.bl_user_input = QLineEdit(ROBOT_CONFIG.main_control_user)
        self.bl_password_input = QLineEdit()
        self.bl_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.bl_robot_id_input = QLineEdit(ROBOT_CONFIG.ws_accid or "HU_D04_01_121")
        self.bl_runs_input = QLineEdit("1")
        for line_edit in (self.bl_host_input, self.bl_user_input, self.bl_password_input, self.bl_robot_id_input, self.bl_runs_input):
            line_edit.setStyleSheet(input_style)
            line_edit.setMaximumWidth(260)
        self.bl_robot_id_input.setMaximumWidth(140)
        self.bl_runs_input.setMaximumWidth(100)
        fields = [
            ("机器人 IP", self.bl_host_input),
            ("SSH 用户", self.bl_user_input),
            ("SSH 密码", self.bl_password_input),
            ("机器人 ID", self.bl_robot_id_input),
            ("检测轮数", self.bl_runs_input),
        ]
        for index, (label_text, editor) in enumerate(fields):
            row = index // 2
            col = (index % 2) * 2
            label = QLabel(label_text)
            label.setStyleSheet("color: #1D2129; font-size: 12px; background: transparent;")
            form.addWidget(label, row, col)
            form.addWidget(editor, row, col + 1)
        bl_layout.addLayout(form)

        bl_btn_grid = QGridLayout()
        bl_btn_grid.setHorizontalSpacing(10)
        bl_btn_grid.setVerticalSpacing(10)
        bl_button_style = (
            "QPushButton { background: #6C5CE7; color: #fff; padding: 9px 16px; "
            "border-radius: 6px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #5A4BD1; }"
            "QPushButton:disabled { background: #C9CDD4; color: #FFF; }")
        self.bl_connect_btn = QPushButton("准备环境")
        self.bl_start_btn = QPushButton("开始检测")
        self.bl_refresh_btn = QPushButton("刷新状态")
        self.bl_download_btn = QPushButton("下载结果")
        self.bl_disconnect_btn = QPushButton("断开")
        for button in (
            self.bl_connect_btn, self.bl_start_btn, self.bl_refresh_btn,
            self.bl_download_btn, self.bl_disconnect_btn,
        ):
            button.setStyleSheet(bl_button_style)
        self.bl_connect_btn.clicked.connect(self._connect_backlash)
        self.bl_start_btn.clicked.connect(self._start_backlash)
        self.bl_refresh_btn.clicked.connect(self._service.refresh_backlash_state)
        self.bl_download_btn.clicked.connect(self._service.download_backlash_results)
        self.bl_disconnect_btn.clicked.connect(self._service.disconnect_backlash_console)
        self.bl_disconnect_btn.setEnabled(False)
        for index, button in enumerate((
            self.bl_connect_btn, self.bl_start_btn, self.bl_refresh_btn,
            self.bl_download_btn, self.bl_disconnect_btn,
        )):
            button.setMinimumWidth(120)
            bl_btn_grid.addWidget(button, index // 3, index % 3)
        bl_btn_grid.setColumnStretch(3, 1)
        bl_layout.addLayout(bl_btn_grid)

        layout.addWidget(bl_group)

        # ---- Results ----
        result_group = QGroupBox("校零日志")
        result_group.setStyleSheet(group_style)
        result_layout = QVBoxLayout(result_group)
        self.result_log = QTextEdit()
        self.result_log.setReadOnly(True)
        self.result_log.setStyleSheet(
            "QTextEdit { background: #F7F8FA; color: #1D2129; border: 1px solid #E5E6EB; "
            "border-radius: 8px; font-family: 'Consolas', monospace; font-size: 12px; }")
        result_layout.addWidget(self.result_log)
        layout.addWidget(result_group)

        layout.addStretch()

    def _connect_signals(self):
        self._service.calibrate_started.connect(self._on_started)
        self._service.calibrate_result.connect(self._on_result)
        self._service.backlash_state_ready.connect(self._on_backlash_state)
        self._service.backlash_launched.connect(
            lambda path: self.result_log.append(f"[backlash] 已启动: {path}"))

    def _connect_backlash(self):
        self._service.connect_backlash_console(self._backlash_payload())

    def _start_backlash(self):
        self._service.start_backlash_workflow(self._backlash_payload())

    def _backlash_payload(self) -> dict:
        password = self.bl_password_input.text()
        self.bl_password_input.clear()
        payload = {
            "host": self.bl_host_input.text().strip(),
            "username": self.bl_user_input.text().strip(),
            "password": password,
            "robot_id": self.bl_robot_id_input.text().strip(),
            "target_runs": self.bl_runs_input.text().strip() or "1",
        }
        return payload

    def _on_backlash_state(self, state: dict):
        session_state = state.get("session_state", "unknown")
        active_step = state.get("active_step_id") or "-"
        results = state.get("results") or []
        result_count = len(results)
        self.bl_state_label.setText(f"状态: {session_state} | 当前步骤: {active_step} | 本轮结果: {result_count}")
        self.bl_start_btn.setEnabled(session_state in {"ready", "completed", "failed"})
        self.bl_disconnect_btn.setEnabled(session_state != "disconnected")
        if results:
            names = ", ".join(str(item.get("name", item)) for item in results[:5])
            suffix = "..." if len(results) > 5 else ""
            self.result_log.append(f"[backlash] 结果文件: {names}{suffix}")

    def _on_started(self, cal_type: str):
        action_text = "开始校零" if cal_type == "mission_engine" else "开始操作"
        self.result_log.append(f"[{cal_type}] {action_text}...")
        self.ws_calibrate_btn.setEnabled(cal_type != "mission_engine")
        for button in (
            self.bl_connect_btn, self.bl_start_btn, self.bl_refresh_btn,
            self.bl_download_btn, self.bl_disconnect_btn,
        ):
            button.setEnabled(cal_type != "backlash")

    def _on_result(self, cal_type: str, success: bool, detail: str):
        color = "#00B42A" if success else "#F53F3F"
        self.result_log.append(f'[{cal_type}] <span style="color:{color}">{"成功" if success else "失败"}</span>: {detail}')
        self.ws_calibrate_btn.setEnabled(True)
        for button in (
            self.bl_connect_btn, self.bl_start_btn, self.bl_refresh_btn,
            self.bl_download_btn, self.bl_disconnect_btn,
        ):
            button.setEnabled(True)

    def append_log(self, text: str):
        self.result_log.append(text)
