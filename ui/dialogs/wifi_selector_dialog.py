"""WiFi network selector dialog - shows robot networks and lets user pick one."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from network.wifi_manager import WifiManager
from config import ROBOT_CONFIG
from models.robot_profile import resolve_robot_profile
from ui.dialogs.message_dialog import AppMessageBox


class WifiSelectorDialog(QDialog):
    network_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择机器人WiFi")
        self.setMinimumSize(420, 360)
        self.setStyleSheet("""
            QDialog { background: #F8F9FA; }
            QLabel { color: #1D2129; }
        """)
        self._build_ui()
        self._scan()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("可用机器人WiFi网络")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #6C5CE7; border: none; background: transparent;")
        layout.addWidget(title)

        self.status_label = QLabel("正在扫描...")
        self.status_label.setStyleSheet("color: #86909C; background: transparent;")
        layout.addWidget(self.status_label)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget { background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; color: #1D2129; }
            QListWidget::item { padding: 12px; border-bottom: 1px solid #E5E6EB; }
            QListWidget::item:selected { background: #6C5CE7; color: white; }
            QListWidget::item:hover { background: #F2F3F5; }
        """)
        self.list_widget.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self.list_widget)

        btn_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._scan)
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setStyleSheet(
            "QPushButton { background: #6C5CE7; color: #fff; padding: 8px 20px; "
            "border-radius: 6px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #5A4BD1; }"
            "QPushButton:disabled { background: #C9CDD4; }")
        self.connect_btn.clicked.connect(self._on_accept)
        self.connect_btn.setEnabled(False)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        btn_bar.addWidget(self.refresh_btn)
        btn_bar.addStretch()
        btn_bar.addWidget(self.cancel_btn)
        btn_bar.addWidget(self.connect_btn)
        layout.addLayout(btn_bar)

        self.list_widget.currentItemChanged.connect(
            lambda: self.connect_btn.setEnabled(True))

    def _scan(self):
        self.list_widget.clear()
        self.status_label.setText("正在扫描WiFi网络...")
        self.refresh_btn.setEnabled(False)

        networks = WifiManager.scan_robot_networks()

        current_ssid = WifiManager.get_current_ssid()

        if not networks:
            self.status_label.setText("未发现机器人WiFi网络")
            self.refresh_btn.setEnabled(True)
            return

        self.status_label.setText(f"发现 {len(networks)} 个机器人网络")

        for net in sorted(networks, key=lambda n: -n.get("signal", 0)):
            ssid = net["ssid"]
            profile = resolve_robot_profile(ssid)
            model_name = profile.display_name if profile else "暂不支持的型号"
            signal = net.get("signal", 0)
            signal_bar = "█" * (signal // 20) + "░" * (5 - signal // 20)
            status = " [已连接]" if ssid == current_ssid else ""
            text = f"{ssid}  ·  {model_name}  {signal_bar}  {signal}%{status}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ssid)
            self.list_widget.addItem(item)

        self.refresh_btn.setEnabled(True)

    def _on_accept(self):
        item = self.list_widget.currentItem()
        if item:
            ssid = item.data(Qt.ItemDataRole.UserRole)
            self.network_selected.emit(ssid)
            WifiManager.disconnect_robot_networks_except(ssid)
            # Try to connect to the selected robot WiFi
            success = WifiManager.connect_to_wifi(ssid, ROBOT_CONFIG.wifi_password)
            if success:
                self.accept()
            else:
                # Connection command may fail but user might already be connected
                # or Windows will connect asynchronously — accept anyway
                AppMessageBox.information(
                    self, "提示",
                    f"已发送连接请求到 {ssid}\n"
                    "如果未自动连接，请手动在系统WiFi中选择该网络。\n密码: 12345678")
                self.accept()
