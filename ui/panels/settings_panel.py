"""Settings panel for connection parameters and preferences."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QFormLayout, QSpinBox,
)
from PyQt6.QtCore import pyqtSignal
from config import ROBOT_CONFIG


class SettingsPanel(QWidget):
    settings_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        title = QLabel("设置")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #1D2129; border: none; background: transparent;")
        layout.addWidget(title)

        # Robot connection settings
        conn_group = QGroupBox("机器人连接设置")
        conn_group.setStyleSheet(
            "QGroupBox { color: #1D2129; font-size: 14px; font-weight: 700; "
            "border: 1px solid #E5E6EB; border-radius: 10px; margin-top: 12px; "
            "padding-top: 24px; background: #FFFFFF; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }")
        form = QFormLayout(conn_group)

        self._fields: dict[str, QLineEdit | QSpinBox] = {}

        fields_def = [
            ("MCP地址", "mcp_url", ROBOT_CONFIG.mcp_url),
            ("WebSocket地址", "websocket_url", ROBOT_CONFIG.websocket_url),
            ("机器人ID (accid)", "ws_accid", ROBOT_CONFIG.ws_accid),
            ("主控IP", "main_control_ip", ROBOT_CONFIG.main_control_ip),
            ("主控用户", "main_control_user", ROBOT_CONFIG.main_control_user),
            ("感知IP", "perception_ip", ROBOT_CONFIG.perception_ip),
            ("感知用户", "perception_user", ROBOT_CONFIG.perception_user),
            ("WiFi SSID前缀", "wifi_ssid_patterns", ", ".join(ROBOT_CONFIG.wifi_ssid_patterns)),
            ("WiFi密码", "wifi_password", ROBOT_CONFIG.wifi_password),
        ]

        for label, key, value in fields_def:
            edit = QLineEdit(value)
            edit.setStyleSheet(
                "QLineEdit { background: #F2F3F5; color: #1D2129; border: 1px solid #E5E6EB; "
                "border-radius: 6px; padding: 8px; }"
                "QLineEdit:focus { border-color: #6C5CE7; background: #FFFFFF; }")
            form.addRow(label, edit)
            self._fields[key] = edit

        # CPU/IMU thresholds
        form.addRow(QLabel(""))
        cpu_spin = QSpinBox()
        cpu_spin.setRange(1, 64)
        cpu_spin.setValue(ROBOT_CONFIG.expected_cpu_cores)
        cpu_spin.setStyleSheet(
            "QSpinBox { background: #F2F3F5; color: #1D2129; border: 1px solid #E5E6EB; "
            "border-radius: 6px; padding: 8px; }"
            "QSpinBox:focus { border-color: #6C5CE7; background: #FFFFFF; }")
        form.addRow("期望CPU核心数", cpu_spin)
        self._fields["expected_cpu_cores"] = cpu_spin

        layout.addWidget(conn_group)

        # Save button
        save_btn = QPushButton("保存设置")
        save_btn.setStyleSheet(
            "QPushButton { background: #6C5CE7; color: #fff; padding: 10px 20px; "
            "border-radius: 6px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #5A4BD1; }")
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

        layout.addStretch()

    def _save_settings(self):
        changes = {}
        for key, field in self._fields.items():
            if isinstance(field, QLineEdit):
                changes[key] = field.text()
            elif isinstance(field, QSpinBox):
                changes[key] = field.value()
        # Update global config
        for key, value in changes.items():
            if key == "wifi_ssid_patterns":
                value = tuple(part.strip() for part in value.split(",") if part.strip())
            if hasattr(ROBOT_CONFIG, key):
                setattr(ROBOT_CONFIG, key, value)
        self.settings_changed.emit(changes)
