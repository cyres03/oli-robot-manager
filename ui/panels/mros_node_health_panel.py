"""Luna mROS node health view backed by the managed test runner."""
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.managed_case import TestCaseDefinition, TestRunResult
from services.managed_test_service import TestCaseService


MROS_HEALTH_CASE_ID = "luna-mros-node-health"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ERROR_MARKERS = (
    "error", "fatal", "failed", "failure", "offline", "disconnect",
    "exception", "crash", "lost", "异常", "掉线",
)
WARNING_MARKERS = ("warn", "timeout", "stale", "unknown", "missing", "警告")


class MrosNodeHealthPanel(QWidget):
    def __init__(self, service: TestCaseService, parent=None):
        super().__init__(parent)
        self._service = service
        self._available = False
        self._active = False
        self._build_ui()
        self._connect_signals()
        self._sync_cases(service.available_cases())

    def _build_ui(self):
        self.setObjectName("mrosNodeHealthPanel")
        self.setStyleSheet(
            "QWidget#mrosNodeHealthPanel { background: #F6F9FF; }"
            "QLabel#pageTitle { color: #1D2A44; font-size: 20px; font-weight: 700; "
            "background: transparent; }"
            "QLabel#pageDescription { color: #6B7A99; font-size: 12px; "
            "background: transparent; }"
            "QLineEdit { background: #FFFFFF; color: #263550; border: 1px solid #D7E3F4; "
            "border-radius: 6px; padding: 8px 12px; }"
            "QLineEdit:focus { border-color: #4F6BED; }"
            "QPushButton { background: #FFFFFF; color: #30415F; border: 1px solid #D7E3F4; "
            "border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
            "QPushButton:hover { background: #EEF4FF; border-color: #8FA8E8; }"
            "QPushButton#primaryBtn { background: #4F6BED; color: #FFFFFF; "
            "border-color: #4F6BED; font-weight: 700; }"
            "QTableWidget#mrosTable { background: #FFFFFF; alternate-background-color: #F8FAFF; "
            "color: #263550; border: 1px solid #D7E3F4; border-radius: 7px; "
            "gridline-color: #E7EDF7; selection-background-color: #E8F0FF; "
            "selection-color: #1D2A44; }"
            "QTableWidget#mrosTable::item { padding: 6px 8px; }"
            "QHeaderView::section { background: #EAF1FF; color: #34518D; border: none; "
            "border-right: 1px solid #D7E3F4; border-bottom: 1px solid #D7E3F4; "
            "padding: 8px; font-weight: 700; }"
            "QLabel#runStatus { color: #4F6BED; background: #EAF1FF; border-radius: 6px; "
            "padding: 6px 10px; font-weight: 600; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        title = QLabel("Luna mROS 节点健康")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        target = QLabel("数据源: limx@10.192.1.2 · mrosconsole | grep -E")
        target.setObjectName("pageDescription")
        layout.addWidget(target)

        toolbar = QHBoxLayout()
        filter_label = QLabel("grep -E")
        self.pattern_input = QLineEdit(".")
        self.pattern_input.setPlaceholderText("输入节点名或状态表达式，例如 node|error")
        self.pattern_input.setClearButtonEnabled(True)
        self.pattern_input.setMaximumWidth(520)
        self.run_btn = QPushButton("读取 .2 节点状态")
        self.run_btn.setObjectName("primaryBtn")
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        toolbar.addWidget(filter_label)
        toolbar.addWidget(self.pattern_input, 1)
        toolbar.addWidget(self.run_btn)
        toolbar.addWidget(self.cancel_btn)
        layout.addLayout(toolbar)

        self.status_label = QLabel("等待读取")
        self.status_label.setObjectName("runStatus")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("mrosTable")
        self.table.setHorizontalHeaderLabels(["状态", "流", "mROS 输出"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 80)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table, 1)

    def _connect_signals(self):
        self.run_btn.clicked.connect(self._run)
        self.cancel_btn.clicked.connect(self._service.cancel_current)
        self._service.cases_changed.connect(self._sync_cases)
        self._service.run_started.connect(self._on_run_started)
        self._service.output_line.connect(self._on_output)
        self._service.run_finished.connect(self._on_run_finished)
        self._service.error_occurred.connect(self._on_error)

    def _sync_cases(self, cases: list[TestCaseDefinition]):
        self._available = any(case.case_id == MROS_HEALTH_CASE_ID for case in cases)
        self._active = False
        self.run_btn.setEnabled(self._available)
        self.cancel_btn.setEnabled(False)
        self.table.setRowCount(0)
        self.status_label.setText(
            "等待读取" if self._available else "当前机器人不提供 mROS 节点健康"
        )

    def _run(self):
        pattern = self.pattern_input.text().strip() or "."
        if len(pattern) > 256 or any(character in pattern for character in "\0\r\n"):
            self.status_label.setText("grep 表达式无效或超过 256 个字符")
            return
        self.table.setRowCount(0)
        self._active = True
        self._service.run_case(
            MROS_HEALTH_CASE_ID,
            arguments_override=(pattern,),
        )

    def _on_run_started(self, case: TestCaseDefinition):
        if case.case_id != MROS_HEALTH_CASE_ID:
            return
        self._active = True
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("正在从 .2 读取 mROS 节点状态...")

    def _on_output(self, line: str, stream: str):
        if not self._active:
            return
        text = ANSI_ESCAPE.sub("", line).strip()
        if not text:
            return
        lowered = text.lower()
        if stream == "stderr" or any(marker in lowered for marker in ERROR_MARKERS):
            state, color = "异常", "#F53F3F"
        elif any(marker in lowered for marker in WARNING_MARKERS):
            state, color = "警告", "#FF7D00"
        else:
            state, color = "正常", "#00B42A"

        row = self.table.rowCount()
        self.table.insertRow(row)
        state_item = QTableWidgetItem(state)
        state_item.setForeground(QColor(color))
        state_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 0, state_item)
        self.table.setItem(row, 1, QTableWidgetItem(stream))
        self.table.setItem(row, 2, QTableWidgetItem(text))
        self.status_label.setText(f"已读取 {row + 1} 行")

    def _on_run_finished(self, result: TestRunResult):
        if result.case_id != MROS_HEALTH_CASE_ID:
            return
        self._active = False
        self.run_btn.setEnabled(self._available)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(
            f"{result.status.value}: {self.table.rowCount()} 行 · {result.detail}"
        )

    def _on_error(self, message: str):
        if not self._active:
            return
        self._active = False
        self.run_btn.setEnabled(self._available)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(message)