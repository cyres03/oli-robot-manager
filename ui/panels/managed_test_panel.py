"""Managed test-case workbench for product-specific robot nodes."""
from datetime import datetime
from pathlib import Path
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.managed_case import TestCaseDefinition, TestRunResult, TestSource
from services.managed_test_service import TestCaseService


NODE_LABELS = {
    "main": ".2 主控",
    "speech_vision": ".4 语音/视觉",
}
READ_ONLY_FILTER = "__read_only__"
CONFIRMATION_REASON_LABELS = {
    "remote_command": "远端命令",
    "local_script": "本地临时脚本",
    "hardware_control": "硬件控制",
    "sudo": "管理员权限",
    "restart": "服务或设备重启",
    "persistent_write": "持久写入",
}
ANSI_ESCAPE = re.compile(
    r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\)|[@-_])"
)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0B-\x1F\x7F]")
STATUS_COLORS = {
    "待执行": ("#64748B", "#F1F5F9"),
    "执行中": ("#3157C8", "#EAF0FF"),
    "PASS": ("#18794E", "#EAF7F0"),
    "FAIL": ("#C2413A", "#FDECEC"),
    "ERROR": ("#C2413A", "#FDECEC"),
    "CANCELLED": ("#64748B", "#F1F5F9"),
}


class TestCasePanel(QWidget):
    def __init__(self, service: TestCaseService, parent=None):
        super().__init__(parent)
        self._service = service
        self._cases: dict[str, TestCaseDefinition] = {}
        self._build_ui()
        self._connect_signals()
        self._populate_cases(service.available_cases())

    def _build_ui(self):
        self.setObjectName("testCasePanel")
        self.setStyleSheet(
            "QWidget#testCasePanel { background: #F6F9FF; }"
            "QLabel#pageTitle { color: #1D2A44; font-size: 20px; font-weight: 700; "
            "background: transparent; }"
            "QLabel#pageDescription { color: #6B7A99; font-size: 12px; "
            "background: transparent; }"
            "QComboBox { background: #FFFFFF; color: #263550; border: 1px solid #D7E3F4; "
            "border-radius: 6px; padding: 7px 12px; min-width: 106px; }"
            "QComboBox:hover, QComboBox:focus { border-color: #4F6BED; }"
            "QPushButton { background: #FFFFFF; color: #30415F; border: 1px solid #D7E3F4; "
            "border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
            "QPushButton:hover { background: #EEF4FF; border-color: #8FA8E8; }"
            "QPushButton:disabled { background: #F3F6FB; color: #A9B4C8; "
            "border-color: #E3EAF5; }"
            "QPushButton#primaryBtn { background: #4F6BED; color: #FFFFFF; "
            "border-color: #4F6BED; font-weight: 700; }"
            "QPushButton#primaryBtn:hover { background: #3E5ACF; border-color: #3E5ACF; }"
            "QTableWidget#testCaseTable { background: #FFFFFF; alternate-background-color: #F8FAFF; "
            "color: #263550; border: 1px solid #D7E3F4; border-radius: 7px; "
            "gridline-color: #E7EDF7; selection-background-color: #E8F0FF; "
            "selection-color: #1D2A44; }"
            "QTableWidget#testCaseTable::item { padding: 7px 8px; }"
            "QHeaderView::section { background: #EAF1FF; color: #34518D; border: none; "
            "border-right: 1px solid #D7E3F4; border-bottom: 1px solid #D7E3F4; "
            "padding: 8px; font-weight: 700; }"
            "QPlainTextEdit#testCaseOutput { background: #F8FAFF; color: #263550; "
            "border: 1px solid #D7E3F4; border-radius: 7px; padding: 10px; "
            "selection-background-color: #DCE7FF; selection-color: #1D2A44; "
            "font-family: Consolas, 'JetBrains Mono', 'Courier New', monospace; "
            "font-size: 12px; }"
            "QLabel#runStatus { color: #4F6BED; background: #EAF1FF; border-radius: 6px; "
            "padding: 6px 10px; font-weight: 600; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        title = QLabel("Luna 测试用例")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.context_label = QLabel("选择由当前机器人 Profile 提供的节点测试用例")
        self.context_label.setObjectName("pageDescription")
        layout.addWidget(self.context_label)

        toolbar = QHBoxLayout()
        self.node_filter = QComboBox()
        self.node_filter.setToolTip("按目标节点筛选")
        self.category_filter = QComboBox()
        self.category_filter.setToolTip("按类别筛选")
        self.risk_filter = QComboBox()
        self.risk_filter.setToolTip("按风险筛选")
        toolbar.addWidget(self.node_filter)
        toolbar.addWidget(self.category_filter)
        toolbar.addWidget(self.risk_filter)
        self.run_btn = QPushButton("运行选中用例")
        self.run_btn.setObjectName("primaryBtn")
        self.run_btn.clicked.connect(self._run_selected)
        self.cancel_btn = QPushButton("取消当前测试")
        self.cancel_btn.clicked.connect(self._service.cancel_current)
        self.cancel_btn.setEnabled(False)
        toolbar.addWidget(self.run_btn)
        toolbar.addWidget(self.cancel_btn)
        toolbar.addStretch()
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("runStatus")
        toolbar.addWidget(self.status_label)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("testCaseTable")
        self.table.setHorizontalHeaderLabels([
            "类别", "测试用例", "目标节点", "风险", "超时", "状态",
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 180)
        self.table.setColumnWidth(4, 80)
        self.table.setMaximumHeight(230)
        layout.addWidget(self.table)

        self.output = QPlainTextEdit()
        self.output.setObjectName("testCaseOutput")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("测试 stdout / stderr 将实时显示在这里")
        self.output.setMaximumBlockCount(10000)
        layout.addWidget(self.output, 1)

    def _connect_signals(self):
        self._service.cases_changed.connect(self._populate_cases)
        self._service.run_started.connect(self._on_run_started)
        self._service.output_line.connect(self._on_output)
        self._service.run_finished.connect(self._on_run_finished)
        self._service.error_occurred.connect(self._on_error)
        self.node_filter.currentIndexChanged.connect(self._apply_filters)
        self.category_filter.currentIndexChanged.connect(self._apply_filters)
        self.risk_filter.currentIndexChanged.connect(self._apply_filters)

    def _populate_cases(self, cases: list[TestCaseDefinition]):
        self._cases = {case.case_id: case for case in cases}
        self._replace_filter_items(
            self.node_filter,
            "全部节点",
            [
                (NODE_LABELS.get(role, role), role)
                for role in sorted({case.target_role for case in cases})
            ],
        )
        self._replace_filter_items(
            self.category_filter,
            "全部类别",
            [(category, category) for category in sorted({case.category for case in cases})],
        )
        risks = sorted({risk.value for case in cases for risk in case.risks})
        risk_items = [("只读", READ_ONLY_FILTER)]
        risk_items.extend((risk, risk) for risk in risks)
        self._replace_filter_items(self.risk_filter, "全部风险", risk_items)
        self._apply_filters()

    @staticmethod
    def _replace_filter_items(combo: QComboBox, all_label: str, items: list[tuple[str, str]]):
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, None)
        for label, value in items:
            combo.addItem(label, value)
        selected_index = combo.findData(selected)
        combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        combo.blockSignals(False)

    def _apply_filters(self):
        node = self.node_filter.currentData()
        category = self.category_filter.currentData()
        risk = self.risk_filter.currentData()
        cases = [
            case for case in self._cases.values()
            if (node is None or case.target_role == node)
            and (category is None or case.category == category)
            and (
                risk is None
                or (risk == READ_ONLY_FILTER and not case.risks)
                or any(flag.value == risk for flag in case.risks)
            )
        ]
        self._render_cases(cases)

    def _render_cases(self, cases: list[TestCaseDefinition]):
        self.table.setRowCount(len(cases))
        for row, case in enumerate(cases):
            risks = ", ".join(sorted(risk.value for risk in case.risks)) or "只读"
            if case.requires_confirmation:
                risks += " · 需确认"
            values = [
                case.category,
                case.name,
                NODE_LABELS.get(case.target_role, case.target_role),
                risks,
                f"{case.timeout_seconds}s",
                "待执行",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, case.case_id)
                self.table.setItem(row, column, item)
            self._set_case_status(case.case_id, "待执行")
        if cases:
            self.table.selectRow(0)
            self.context_label.setText(
                f"显示 {len(cases)} / {len(self._cases)} 个测试用例"
            )
        else:
            self.context_label.setText("没有符合筛选条件的测试用例")
        self.run_btn.setEnabled(bool(cases))

    def _selected_case(self) -> TestCaseDefinition | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)
        case_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self._cases.get(str(case_id))

    def _run_selected(self):
        case = self._selected_case()
        if not case:
            self.status_label.setText("请先选择测试用例")
            return
        approved = False
        if case.requires_confirmation:
            if not self._confirm_execution(case):
                self.status_label.setText("已取消测试")
                return
            approved = True

        local_script = None
        if case.source == TestSource.LOCAL_SCRIPT:
            selected, _ = QFileDialog.getOpenFileName(
                self, "选择测试脚本", "", "Python 脚本 (*.py);;Shell 脚本 (*.sh);;所有文件 (*)",
            )
            if not selected:
                self.status_label.setText("未选择测试脚本")
                return
            local_script = Path(selected)
        self.output.clear()
        self._service.run_case(case.case_id, approved, local_script)

    def _confirm_execution(self, case: TestCaseDefinition) -> bool:
        reasons = "、".join(
            CONFIRMATION_REASON_LABELS.get(reason, reason)
            for reason in case.confirmation_reasons
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("确认测试")
        box.setText(case.name)
        box.setInformativeText(
            f"确认原因：{reasons}\n\n确认目标机器人和现场安全后再继续。"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        confirm_button = box.button(QMessageBox.StandardButton.Yes)
        cancel_button = box.button(QMessageBox.StandardButton.No)
        if confirm_button:
            confirm_button.setText("确认执行")
            confirm_button.setObjectName("confirmButton")
        if cancel_button:
            cancel_button.setText("取消")
            cancel_button.setObjectName("cancelButton")
        box.setStyleSheet(
            "QMessageBox { background: #FFFFFF; color: #1D2129; }"
            "QMessageBox QLabel { color: #1D2129; background: transparent; "
            "font-size: 14px; min-width: 420px; }"
            "QMessageBox QPushButton { min-width: 96px; min-height: 36px; "
            "border-radius: 6px; padding: 6px 18px; font-size: 14px; font-weight: 600; }"
            "QMessageBox QPushButton#confirmButton { background: #6C5CE7; "
            "color: #FFFFFF; border: 1px solid #6C5CE7; }"
            "QMessageBox QPushButton#confirmButton:hover { background: #5A4BD1; "
            "border-color: #5A4BD1; }"
            "QMessageBox QPushButton#cancelButton { background: #FFFFFF; "
            "color: #1D2129; border: 1px solid #C9CDD4; }"
            "QMessageBox QPushButton#cancelButton:hover { background: #F2F3F5; "
            "border-color: #A9AEB8; }"
        )
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_run_started(self, case: TestCaseDefinition):
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(f"执行中: {case.name}")
        self._set_case_status(case.case_id, "执行中")
        self.output.appendPlainText(
            f"开始: {datetime.now().isoformat(timespec='seconds')}"
        )

    def _on_output(self, line: str, stream: str):
        prefix = "[stderr] " if stream == "stderr" else ""
        clean_line = CONTROL_CHARACTERS.sub("", ANSI_ESCAPE.sub("", line)).strip()
        if clean_line:
            self.output.appendPlainText(prefix + clean_line)

    def _on_run_finished(self, result: TestRunResult):
        self.run_btn.setEnabled(bool(self._cases))
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(
            f"{result.status.value}: {result.case_id} · {result.detail}"
        )
        self._set_case_status(result.case_id, result.status.value)
        exit_code = "无" if result.exit_code is None else str(result.exit_code)
        self.output.appendPlainText(
            "\n".join([
                "",
                f"状态: {result.status.value}",
                f"开始: {result.started_at}",
                f"结束: {result.completed_at}",
                f"退出码: {exit_code}",
                f"会话: {result.session_id}",
                f"机器人: {result.accid}",
                f"固件: {result.firmware}",
                f"目标: {NODE_LABELS.get(result.target_role, result.target_role)} "
                f"({result.target_host})",
            ])
        )
        if result.artifacts:
            self.output.appendPlainText("产物:\n" + "\n".join(result.artifacts))

    def _on_error(self, message: str):
        self.run_btn.setEnabled(bool(self._cases))
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(message)

    def _set_case_status(self, case_id: str, status: str):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) == case_id:
                status_item = self.table.item(row, 5)
                status_item.setText(status)
                foreground, background = STATUS_COLORS.get(
                    status, STATUS_COLORS["待执行"]
                )
                status_item.setForeground(QBrush(QColor(foreground)))
                status_item.setBackground(QBrush(QColor(background)))
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                return