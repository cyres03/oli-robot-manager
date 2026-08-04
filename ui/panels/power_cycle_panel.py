"""Power cycle test panel with countdown and before/after comparison."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar,
)
from PyQt6.QtCore import Qt
from services.power_cycle_service import PowerCycleService, PowerCycleState
from models.health import HealthCheckResult
from ui.widgets.health_result_view import HealthResultView


class PowerCyclePanel(QWidget):
    def __init__(self, power_cycle_service: PowerCycleService, parent=None):
        super().__init__(parent)
        self._service = power_cycle_service
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        title = QLabel("断电重启测试")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #1D2129; border: none; background: transparent;")
        layout.addWidget(title)

        desc = QLabel("测试断电前后机器人状态: 关机→5分钟→开机→自动重诊断")
        desc.setStyleSheet("color: #86909C; font-size: 13px; border: none; background: transparent;")
        layout.addWidget(desc)

        ctrl_bar = QHBoxLayout()
        self.start_btn = QPushButton("开始断电测试")
        self.start_btn.setStyleSheet(
            "QPushButton { background: #6C5CE7; color: #fff; padding: 12px 24px; "
            "border-radius: 6px; font-size: 15px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #5A4BD1; }"
            "QPushButton:disabled { background: #C9CDD4; }")
        self.start_btn.clicked.connect(self._service.start)

        self.confirm_off_btn = QPushButton("已关机")
        self.confirm_off_btn.setStyleSheet(
            "QPushButton { background: #F53F3F; color: #fff; padding: 12px 24px; "
            "border-radius: 6px; font-size: 15px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #E03434; }")
        self.confirm_off_btn.setEnabled(False)
        self.confirm_off_btn.clicked.connect(self._service.confirm_power_off)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._service.cancel)

        ctrl_bar.addWidget(self.start_btn)
        ctrl_bar.addWidget(self.confirm_off_btn)
        ctrl_bar.addStretch()
        ctrl_bar.addWidget(self.cancel_btn)
        layout.addLayout(ctrl_bar)

        # Status
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #4E5969; font-size: 13px; padding: 8px 0; background: transparent;")
        layout.addWidget(self.status_label)

        # Countdown progress
        self.countdown_bar = QProgressBar()
        self.countdown_bar.setRange(0, 300)
        self.countdown_bar.setValue(300)
        self.countdown_bar.setFormat("倒计时: %v 秒")
        self.countdown_bar.setVisible(False)
        self.countdown_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #E5E6EB; border-radius: 6px;
                background: #F2F3F5; color: #6C5CE7; height: 24px;
                text-align: center; font-size: 14px; font-weight: bold;
            }
            QProgressBar::chunk { background: #6C5CE7; border-radius: 5px; }
        """)
        layout.addWidget(self.countdown_bar)

        # Before/After comparison
        comp_layout = QHBoxLayout()

        before_group = QVBoxLayout()
        before_label = QLabel("断电前")
        before_label.setStyleSheet("color: #4E5969; font-size: 14px; font-weight: 600; background: transparent;")
        before_group.addWidget(before_label)
        self.before_view = HealthResultView()
        before_group.addWidget(self.before_view)

        after_group = QVBoxLayout()
        after_label = QLabel("重启后")
        after_label.setStyleSheet("color: #4E5969; font-size: 14px; font-weight: 600; background: transparent;")
        after_group.addWidget(after_label)
        self.after_view = HealthResultView()
        after_group.addWidget(self.after_view)

        comp_layout.addLayout(before_group)
        comp_layout.addLayout(after_group)
        layout.addLayout(comp_layout)

        layout.addStretch()

    def _connect_signals(self):
        self._service.state_changed.connect(self._on_state_changed)
        self._service.countdown_tick.connect(self._on_tick)
        self._service.initial_check_complete.connect(self._on_initial)
        self._service.final_check_complete.connect(self._on_final)
        self._service.comparison_ready.connect(self._on_comparison)
        self._service.error_occurred.connect(self._on_error)

    def _on_state_changed(self, state: PowerCycleState, description: str):
        self.status_label.setText(description)

        self.start_btn.setEnabled(state == PowerCycleState.IDLE)
        self.confirm_off_btn.setEnabled(state == PowerCycleState.WAITING_POWER_OFF)
        self.countdown_bar.setVisible(state == PowerCycleState.COUNTDOWN)

        if state == PowerCycleState.COUNTDOWN:
            self.countdown_bar.setValue(300)

    def _on_tick(self, remaining: int):
        self.countdown_bar.setValue(remaining)

    def _on_initial(self, result: HealthCheckResult):
        self.before_view.show_result(result)

    def _on_final(self, result: HealthCheckResult):
        self.after_view.show_result(result)

    def _on_comparison(self, before: HealthCheckResult, after: HealthCheckResult):
        before_ok = "通过" if before.all_passed else "失败"
        after_ok = "通过" if after.all_passed else "失败"
        self.status_label.setText(f"测试完成! 断电前: {before_ok} → 重启后: {after_ok}")

    def _on_error(self, error: str):
        self.status_label.setText(f"错误: {error}")
        self.status_label.setStyleSheet("color: #F53F3F; font-size: 13px; background: transparent;")
