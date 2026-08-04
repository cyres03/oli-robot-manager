"""Prominent robot status banner — always visible at top of content area."""
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget
from PyQt6.QtCore import Qt


class StatusBanner(QFrame):
    STATUS_MAP = {
        "ZeroTorque": ("零力矩", "#86909C"),
        "Damping": ("阻尼模式", "#00B42A"),
        "Walk": ("行走中", "#6C5CE7"),
        "Stand": ("站立", "#6C5CE7"),
        "Sit": ("坐姿", "#FF7D00"),
        "Action": ("动作执行中", "#6C5CE7"),
        "Menu": ("Menu", "#6C5CE7"),
        "Prepare": ("准备中", "#FF7D00"),
        "Remote": ("遥控模式", "#00B42A"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_status_key = None
        self.setObjectName("statusBanner")
        self.setFixedHeight(44)
        self.setStyleSheet("""
            #statusBanner {
                background: #FFFFFF; border-bottom: 1px solid #E5E6EB;
                border-radius: 0; padding: 0;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(16)

        # Robot SN
        self.sn_label = self._make_label("", "#6C5CE7", 14, 700)

        # Status pill
        self.status_pill = QFrame()
        self.status_pill.setObjectName("statusPill")
        self.status_pill.setStyleSheet(
            "#statusPill { background: #F2F3F5; border-radius: 12px; padding: 4px 16px; }")
        pill_layout = QHBoxLayout(self.status_pill)
        pill_layout.setContentsMargins(14, 4, 14, 4)
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #86909C; font-size: 12px; border: none; background: transparent;")
        pill_layout.addWidget(self.status_dot)
        self.status_text = QLabel("未连接")
        self.status_text.setStyleSheet("color: #86909C; font-size: 13px; font-weight: 700; border: none; background: transparent;")
        pill_layout.addWidget(self.status_text)
        layout.addWidget(self.status_pill)

        # Ability
        self.ability_label = self._make_label("", "#4E5969", 12, 500)
        layout.addWidget(self.ability_label)

        layout.addStretch()

        # Battery
        self.battery_label = self._make_label("", "#4E5969", 13, 700)
        layout.addWidget(self.battery_label)

        # IMU status
        self.imu_label = self._make_label("", "#4E5969", 11, 400)
        layout.addWidget(self.imu_label)

        # Mode
        self.mode_label = self._make_label("", "#86909C", 11, 400)
        layout.addWidget(self.mode_label)

    def _make_label(self, text, color, size, weight):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {color}; font-size: {size}px; font-weight: {weight}; "
            "border: none; background: transparent;")
        return lbl

    def update_status(self, info: dict):
        sn = info.get("sn", "?")
        status = info.get("robot_status", "?")
        ability = info.get("ability", "?")
        mode = info.get("mode", "?")
        battery_pct = info.get("battery_pct", 0)
        imu = info.get("imu_status", "")
        status_key = (sn, status, ability, mode, battery_pct, imu)
        if status_key == self._last_status_key:
            return
        self._last_status_key = status_key

        if sn and sn != "?":
            self.sn_label.setText(sn)

        # Status pill
        cn_name, color = self.STATUS_MAP.get(status, (status, "#86909C"))
        self.status_text.setText(cn_name)
        self.status_text.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 700; border: none; background: transparent;")
        self.status_dot.setStyleSheet(
            f"color: {color}; font-size: 12px; border: none; background: transparent;")

        # Ability
        self.ability_label.setText(f"控制器: {ability}")

        # Battery with color
        if battery_pct >= 50:
            b_color = "#00B42A"
        elif battery_pct >= 20:
            b_color = "#FF7D00"
        else:
            b_color = "#F53F3F"
        self.battery_label.setText(f"电量 {battery_pct}%")
        self.battery_label.setStyleSheet(
            f"color: {b_color}; font-size: 13px; font-weight: 700; border: none; background: transparent;")

        # IMU
        if imu:
            imu_ok = imu == "OK"
            self.imu_label.setText(f"IMU: {'OK' if imu_ok else imu}")
            self.imu_label.setStyleSheet(
                f"color: {'#00B42A' if imu_ok else '#F53F3F'}; font-size: 11px; "
                "border: none; background: transparent;")

        # Mode
        self.mode_label.setText(f"模式: {mode}")

    def set_disconnected(self):
        self.status_dot.setStyleSheet("color: #F53F3F; font-size: 12px; border: none; background: transparent;")
        self.status_text.setText("未连接")
        self.status_text.setStyleSheet("color: #F53F3F; font-size: 13px; font-weight: 700; border: none; background: transparent;")
        self.sn_label.setText("")
        self.ability_label.setText("")
        self.battery_label.setText("")
