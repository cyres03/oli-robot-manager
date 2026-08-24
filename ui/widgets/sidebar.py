"""Vertical sidebar navigation."""
import os

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QPushButton, QLabel, QWidget, QHBoxLayout
from PyQt6.QtCore import pyqtSignal, QTimer, QEasingCurve, QPropertyAnimation, QRect, QVariantAnimation, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtSvgWidgets import QSvgWidget
from models.robot_profile import CapabilityState, RobotProfile
from models.workspace import WorkspaceDefinition
from network.wifi_manager import WifiManager


SIDEBAR_COLOR_PRESETS = {
    "mist": {
        "idle_bg": "#F9FAFB",
        "idle_border": "#EEF1F4",
        "hover_bg": "#F1F5F9",
        "hover_border": "#DCE3EA",
        "text": "#52606D",
        "hover_text": "#1F2937",
        "active_bg": "#EEF2FF",
        "active_border": "#CDD6F8",
        "active_text": "#5B61D6",
        "active_text_pressed": "#484EBE",
        "indicator": "#6674F4",
    },
    "slate": {
        "idle_bg": "#F6F8FA",
        "idle_border": "#E7ECF1",
        "hover_bg": "#EEF2F6",
        "hover_border": "#D5DDE6",
        "text": "#4B5563",
        "hover_text": "#111827",
        "active_bg": "#E8EEF8",
        "active_border": "#C6D4EA",
        "active_text": "#375A7F",
        "active_text_pressed": "#294766",
        "indicator": "#4C78A8",
    },
    "lavender": {
        "idle_bg": "#FBFAFE",
        "idle_border": "#F0EAF8",
        "hover_bg": "#F6F2FC",
        "hover_border": "#E4D9F6",
        "text": "#5B6170",
        "hover_text": "#2F3340",
        "active_bg": "#F1EBFD",
        "active_border": "#D9CDF8",
        "active_text": "#7457D9",
        "active_text_pressed": "#5E44BF",
        "indicator": "#8A63E8",
    },
    "mint": {
        "idle_bg": "#F8FBFA",
        "idle_border": "#E6F0ED",
        "hover_bg": "#EEF7F4",
        "hover_border": "#D4E9E1",
        "text": "#52646A",
        "hover_text": "#1F2F34",
        "active_bg": "#E7F6F0",
        "active_border": "#BFE5D7",
        "active_text": "#2B8A6E",
        "active_text_pressed": "#1F6D56",
        "indicator": "#33A884",
    },
}

ACTIVE_SIDEBAR_COLOR_PRESET = "mist"


def _mix_color(start: str, end: str, progress: float) -> QColor:
    progress = max(0.0, min(progress, 1.0))
    a = QColor(start)
    b = QColor(end)
    return QColor(
        round(a.red() + (b.red() - a.red()) * progress),
        round(a.green() + (b.green() - a.green()) * progress),
        round(a.blue() + (b.blue() - a.blue()) * progress),
        round(a.alpha() + (b.alpha() - a.alpha()) * progress),
    )


class SidebarNavButton(QPushButton):
    def __init__(self, text: str, palette: dict[str, str], parent=None):
        super().__init__(text, parent)
        self._palette = palette
        self._hover_progress = 0.0
        self._press_progress = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(150)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_animation.valueChanged.connect(self._set_hover_progress)

        self._press_animation = QVariantAnimation(self)
        self._press_animation.setDuration(90)
        self._press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_animation.valueChanged.connect(self._set_press_progress)

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        self._animate_press(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._animate_press(1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._animate_press(0.0)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        checked = self.isChecked()
        hover = max(self._hover_progress, 1.0 if checked else 0.0)
        press = self._press_progress
        background = _mix_color(self._palette["idle_bg"], self._palette["hover_bg"], hover)
        border = _mix_color(self._palette["idle_border"], self._palette["hover_border"], hover)
        text_color = _mix_color(self._palette["text"], self._palette["hover_text"], hover)

        if checked:
            background = _mix_color(self._palette["active_bg"], self._palette["hover_bg"], press * 0.18)
            border = _mix_color(self._palette["active_border"], self._palette["indicator"], press * 0.2)
            text_color = _mix_color(self._palette["active_text"], self._palette["active_text_pressed"], press * 0.35)

        content_shift = int(round(4 + hover * 6 - press * 2))
        background_rect = self.rect().adjusted(0, 2, -1, -2)
        if press > 0:
            background_rect.translate(1, 1)

        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(background_rect, 8, 8)

        text_rect = background_rect.adjusted(16 + content_shift, 0, -12, 0)
        painter.setPen(text_color)
        painter.setFont(self.font())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())

    def _animate_hover(self, target: float):
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def _animate_press(self, target: float):
        self._press_animation.stop()
        self._press_animation.setStartValue(self._press_progress)
        self._press_animation.setEndValue(target)
        self._press_animation.start()

    def _set_hover_progress(self, value):
        self._hover_progress = float(value)
        self.update()

    def _set_press_progress(self, value):
        self._press_progress = float(value)
        self.update()


class Sidebar(QFrame):
    navigation_requested = pyqtSignal(str)

    ITEMS = [
        ("dance_library", "舞蹈&动作库"),
        ("controls", "基础控制"),
        ("acceptance", "验收测试"),
        ("health_check", "健康检查"),
        ("calibrate", "校零"),
        ("settings", "设置"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nav_palette = SIDEBAR_COLOR_PRESETS[ACTIVE_SIDEBAR_COLOR_PRESET]
        self.setObjectName("sidebar")
        self.setFixedWidth(180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(1)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 4, 16, 8)
        header_layout.setSpacing(10)

        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "resources",
            "logo",
            "oli_manager_logo.svg",
        )
        logo_icon = QSvgWidget(logo_path)
        logo_icon.setFixedSize(34, 34)
        header_layout.addWidget(logo_icon)

        logo = QLabel("OLI ROBOT MANAGER")
        logo.setObjectName("sidebarLogo")
        header_layout.addWidget(logo)
        header_layout.addStretch()
        layout.addWidget(header)

        self.wifi_label = QLabel("  WiFi: 检测中...")
        self.wifi_label.setObjectName("sidebarWifi")
        layout.addWidget(self.wifi_label)

        self.wifi_btn = QPushButton("  选择机器人WiFi")
        self.wifi_btn.setObjectName("sidebarWifiBtn")
        self.wifi_btn.clicked.connect(lambda: self.navigation_requested.emit("wifi_selector"))
        layout.addWidget(self.wifi_btn)

        layout.addSpacing(12)

        self._buttons: dict[str, SidebarNavButton] = {}
        self._indicator = QFrame(self)
        self._indicator.setObjectName("sidebarIndicator")
        self._indicator.setStyleSheet(
            f"background: {self._nav_palette['indicator']}; border-radius: 2px;"
        )
        self._indicator.hide()
        self._indicator_animation = QPropertyAnimation(self._indicator, b"geometry", self)
        self._indicator_animation.setDuration(180)
        self._indicator_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        for key, label in self.ITEMS:
            btn = SidebarNavButton(f"  {label}", self._nav_palette)
            btn.setObjectName("sidebarNav")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self._on_click(k))
            layout.addWidget(btn)
            self._buttons[key] = btn

        layout.addStretch()

        self.ssh_section = QLabel("  快捷SSH")
        self.ssh_section.setObjectName("sidebarSectionLabel")
        layout.addWidget(self.ssh_section)

        self._ssh_buttons: list[QPushButton] = []
        for _ in range(2):
            ssh_btn = QPushButton()
            ssh_btn.setObjectName("sidebarSshBtn")
            ssh_btn.hide()
            layout.addWidget(ssh_btn)
            self._ssh_buttons.append(ssh_btn)

        self.set_active("dance_library")
        QTimer.singleShot(0, lambda: self._move_indicator(self._buttons["dance_library"], animate=False))
        self.refresh_wifi_status()

        self._wifi_timer = QTimer(self)
        self._wifi_timer.timeout.connect(self.refresh_wifi_status)
        self._wifi_timer.start(5000)

    def apply_profile(self, profile: RobotProfile | None):
        nodes = [profile.main_node, *profile.companion_nodes] if profile else []
        self.ssh_section.setVisible(bool(nodes))
        for index, button in enumerate(self._ssh_buttons):
            try:
                button.clicked.disconnect()
            except TypeError:
                pass
            if index >= len(nodes):
                button.hide()
                continue
            node = nodes[index]
            button.setText(f"  {node.label} ({node.username}@{node.host})")
            button.clicked.connect(
                lambda checked, host=node.host, user=node.username: self._on_ssh(host, user)
            )
            button.show()
        robot_pages_enabled = profile is not None
        for key in ("dance_library", "controls", "acceptance", "health_check"):
            self._buttons[key].setEnabled(robot_pages_enabled)
        self._buttons["calibrate"].setEnabled(bool(
            profile and profile.capability("calibration") == CapabilityState.SUPPORTED
        ))

    def apply_workspace(self, workspace: WorkspaceDefinition):
        route_map = {route.key: route.label for route in workspace.routes}
        for key, button in self._buttons.items():
            visible = key in route_map
            button.setVisible(visible)
            if visible:
                button.setText(f"  {route_map[key]}")
        if "log_analysis" in route_map and "log_analysis" not in self._buttons:
            button = SidebarNavButton(
                f"  {route_map['log_analysis']}", self._nav_palette,
            )
            button.setObjectName("sidebarNav")
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked: self._on_click("log_analysis")
            )
            layout = self.layout()
            settings_index = layout.indexOf(self._buttons["settings"])
            layout.insertWidget(settings_index, button)
            self._buttons["log_analysis"] = button
        elif "log_analysis" in self._buttons:
            self._buttons["log_analysis"].setVisible("log_analysis" in route_map)
            if "log_analysis" in route_map:
                self._buttons["log_analysis"].setText(
                    f"  {route_map['log_analysis']}"
                )
        active = workspace.default_route
        if active in self._buttons:
            self.set_active(active)

    def refresh_wifi_status(self):
        ssid = WifiManager.get_robot_ssid() or WifiManager.get_current_ssid()
        is_robot = WifiManager.is_robot_wifi()
        if ssid:
            color = "#00B42A" if is_robot else "#FF7D00"
            self.wifi_label.setText(f"  WiFi: {ssid}")
            self.wifi_label.setStyleSheet(
                f"color: {color}; font-size: 11px; padding: 4px 20px; background: transparent;")
        else:
            self.wifi_label.setText("  WiFi: 未连接")
            self.wifi_label.setStyleSheet("color: #F53F3F; font-size: 11px; padding: 4px 20px; background: transparent;")

    def _on_click(self, key: str):
        self.set_active(key)
        self.navigation_requested.emit(key)

    def _on_ssh(self, host: str, user: str):
        self.navigation_requested.emit(f"ssh:{user}@{host}")

    def set_active(self, key: str):
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)

        button = self._buttons.get(key)
        if button:
            self._move_indicator(button, animate=self._indicator.isVisible())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        current = next((btn for btn in self._buttons.values() if btn.isChecked()), None)
        if current:
            self._move_indicator(current, animate=False)

    def _move_indicator(self, button: QPushButton, animate: bool):
        target = self._indicator_rect(button)
        if not self._indicator.isVisible():
            self._indicator.setGeometry(target)
            self._indicator.show()
            return

        if animate:
            self._indicator_animation.stop()
            self._indicator_animation.setStartValue(self._indicator.geometry())
            self._indicator_animation.setEndValue(target)
            self._indicator_animation.start()
            return

        self._indicator.setGeometry(target)

    def _indicator_rect(self, button: QPushButton) -> QRect:
        geometry = button.geometry()
        return QRect(
            geometry.left() + 4,
            geometry.top() + 8,
            4,
            max(geometry.height() - 16, 18),
        )
