"""Health check diagnostic panel."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QHBoxLayout
from services.health_check_service import HealthCheckService, HealthCheckState
from models.health import HealthCheckResult
from ui.widgets.health_result_view import HealthResultView


class HealthCheckPanel(QWidget):
    def __init__(self, health_service: HealthCheckService, parent=None):
        super().__init__(parent)
        self._service = health_service
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        title = QLabel("机器人健康检查诊断")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #1D2129; border: none; background: transparent;")
        layout.addWidget(title)

        desc = QLabel("只读检测: WiFi连接、CPU核心数、系统时间、IMU频率")
        desc.setStyleSheet("color: #86909C; font-size: 13px; border: none; background: transparent;")
        layout.addWidget(desc)

        ctrl_bar = QHBoxLayout()
        self.run_btn = QPushButton("运行只读健康检查")
        self.run_btn.setStyleSheet(
            "QPushButton { background: #6C5CE7; color: #fff; padding: 12px 24px; "
            "border-radius: 6px; font-size: 15px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #5A4BD1; }"
            "QPushButton:disabled { background: #C9CDD4; }")
        self.run_btn.clicked.connect(
            lambda: self._service.run_full_diagnostic(allow_repairs=False)
        )
        ctrl_bar.addWidget(self.run_btn)
        ctrl_bar.addStretch()
        layout.addLayout(ctrl_bar)

        self.progress_label = QLabel("就绪")
        self.progress_label.setStyleSheet("color: #4E5969; font-size: 13px; padding: 8px 0; background: transparent;")
        layout.addWidget(self.progress_label)

        self.result_view = HealthResultView()
        layout.addWidget(self.result_view)

        layout.addStretch()

    def _connect_signals(self):
        self._service.step_started.connect(self._on_step_started)
        self._service.step_result.connect(self._on_step_result)
        self._service.diagnostic_complete.connect(self._on_complete)
        self._service.diagnostic_error.connect(self._on_error)

    def _on_step_started(self, state: HealthCheckState, description: str):
        self.progress_label.setText(f"执行中: {description}")
        self.run_btn.setEnabled(False)

    def _on_step_result(self, state: HealthCheckState, result: dict):
        passed = result.get("passed", False)
        status_text = "通过" if passed else "失败"
        name = state.name.replace("_", " ").title()
        detail = result.get("message", "") or result.get("error", "") or ""
        self.progress_label.setText(f"[{name}] {status_text} {detail}")

    def _on_complete(self, result: HealthCheckResult):
        self.result_view.show_result(result)
        final = "全部检查通过!" if result.all_passed else "诊断完成,存在失败项"
        self.progress_label.setText(final)
        self.run_btn.setEnabled(True)

    def _on_error(self, error: str):
        self.progress_label.setText(f"错误: {error}")
        self.progress_label.setStyleSheet("color: #F53F3F; font-size: 13px; background: transparent;")
        self.run_btn.setEnabled(True)
