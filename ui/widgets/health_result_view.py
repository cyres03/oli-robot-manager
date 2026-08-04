"""Health check results dashboard view."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QFrame,
)
from models.health import HealthCheckResult


class HealthResultView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self._grid = QGridLayout()
        self._grid.setSpacing(12)

        # Headers
        headers = ["CPU核心", "双目相机", "系统时间", "IMU频率", "总体"]
        for i, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setStyleSheet("color: #4E5969; font-size: 12px; font-weight: 600; background: transparent;")
            self._grid.addWidget(lbl, 0, i)

        # Result cards (placeholders)
        self._cards: dict[str, QFrame] = {}
        card_style = """
            QFrame {
                background: #FFFFFF; border-radius: 10px;
                border: 1px solid #E5E6EB; padding: 16px;
            }
        """
        for i, key in enumerate(["cpu", "camera", "time", "imu", "overall"]):
            card = QFrame()
            card.setStyleSheet(card_style)
            card.setMinimumSize(200, 100)
            card_layout = QVBoxLayout(card)
            self._cards[key] = card
            self._grid.addWidget(card, 1, i)

        layout.addLayout(self._grid)
        layout.addStretch()

    def update_step(self, state, result: dict):
        """Called from HealthCheckService.step_result signal."""
        # state is HealthCheckState enum, result has passed/error/details
        pass

    def show_result(self, result: HealthCheckResult):
        """Display full HealthCheckResult."""
        self._fill_card("cpu", self._format_cpu(result))
        self._fill_card("camera", self._format_camera(result))
        self._fill_card("time", self._format_time(result))
        self._fill_card("imu", self._format_imu(result))
        self._fill_card("overall", self._format_overall(result))

    def _fill_card(self, key: str, text: str):
        card = self._cards.get(key)
        if not card:
            return
        # Clear and rebuild
        while card.layout().count():
            item = card.layout().takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #1D2129; font-size: 13px; background: transparent;")
        lbl.setWordWrap(True)
        card.layout().addWidget(lbl)

    def _format_cpu(self, r: HealthCheckResult) -> str:
        if not r.cpu_result:
            return "等待检测..."
        c = r.cpu_result
        status = "PASS" if c.passed else "FAIL"
        color = "#00B42A" if c.passed else "#F53F3F"
        return f'检测: {c.detected_cores}/{c.expected_cores} 核<br>状态: <span style="color:{color}; font-weight:700;">{status}</span>'

    def _format_camera(self, r: HealthCheckResult) -> str:
        if not r.camera_result:
            return "等待检测..."
        c = r.camera_result
        status = "PASS" if c.passed else "FAIL"
        color = "#00B42A" if c.passed else "#F53F3F"
        usb = "USB3.0" if c.usb3_detected else "USB2.0"
        return (
            f'相机数: {c.camera_count}/{c.expected_count}<br>'
            f'带宽: {usb}<br>'
            f'3次一致: {"是" if c.consistent else "否"}<br>'
            f'状态: <span style="color:{color}; font-weight:700;">{status}</span>'
        )

    def _format_time(self, r: HealthCheckResult) -> str:
        if not r.time_result:
            return "等待检测..."
        t = r.time_result
        status = "PASS" if t.passed else "FAIL"
        color = "#00B42A" if t.passed else "#F53F3F"
        return (
            f'机器人: {t.robot_time or "N/A"}<br>'
            f'本地: {t.local_time or "N/A"}<br>'
            f'偏差: {t.diff_seconds:.0f}s<br>'
            f'状态: <span style="color:{color}; font-weight:700;">{status}</span>'
        )

    def _format_imu(self, r: HealthCheckResult) -> str:
        if not r.imu_result:
            return "等待检测..."
        imu = r.imu_result
        status = "PASS" if imu.passed else "FAIL"
        color = "#00B42A" if imu.passed else "#F53F3F"
        return (
            f'频率: {imu.detected_frequency:.1f} Hz<br>'
            f'期望: {imu.expected_frequency} Hz<br>'
            f'状态: <span style="color:{color}; font-weight:700;">{status}</span>'
        )

    def _format_overall(self, r: HealthCheckResult) -> str:
        status = "全部通过" if r.all_passed else "存在失败项"
        color = "#00B42A" if r.all_passed else "#F53F3F"
        return f'<span style="color:{color}; font-size:16px; font-weight:700;">{status}</span>'

    def clear(self):
        for key in self._cards:
            self._fill_card(key, "等待检测...")
