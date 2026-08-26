from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.repository import AcceptanceSessionRepository
from models.acceptance import AcceptanceItemStatus, AcceptanceSession


class AcceptanceHistoryPanel(QWidget):
    rerun_requested = pyqtSignal(list)

    def __init__(
        self,
        repository: AcceptanceSessionRepository,
        profile_key: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._repository = repository
        self._profile_key = profile_key
        self._selected_session: AcceptanceSession | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        tools = QHBoxLayout()
        self.scope_label = QLabel()
        self.scope_label.setStyleSheet("color: #4E5969; background: transparent;")
        tools.addWidget(self.scope_label)
        tools.addStretch()
        refresh_button = QPushButton("刷新历史")
        refresh_button.clicked.connect(self.refresh)
        tools.addWidget(refresh_button)
        self.rerun_failed_button = QPushButton("复验失败项")
        self.rerun_failed_button.setEnabled(False)
        self.rerun_failed_button.clicked.connect(self._rerun_failed)
        tools.addWidget(self.rerun_failed_button)
        layout.addLayout(tools)

        self.session_table = QTableWidget(0, 6)
        self.session_table.setHorizontalHeaderLabels(
            ["开始时间", "机器人", "状态", "PASS", "FAIL", "N/A"]
        )
        self.session_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.session_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.session_table.verticalHeader().setVisible(False)
        self.session_table.horizontalHeader().setStretchLastSection(True)
        self.session_table.itemSelectionChanged.connect(self._load_selected)
        layout.addWidget(self.session_table, 1)

        self.detail_label = QLabel("选择一条会话查看检查结果")
        self.detail_label.setStyleSheet("color: #4E5969; background: transparent;")
        layout.addWidget(self.detail_label)

        self.result_table = QTableWidget(0, 6)
        self.result_table.setHorizontalHeaderLabels(
            ["模块", "测试项", "状态", "摘要", "执行时间", "备注"]
        )
        self.result_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.result_table, 1)

    def apply_profile(self, profile_key: str):
        if profile_key == self._profile_key:
            return
        self._profile_key = profile_key
        self.refresh()

    def refresh(self):
        self._selected_session = None
        self.rerun_failed_button.setEnabled(False)
        self.result_table.setRowCount(0)
        self.detail_label.setText("选择一条会话查看检查结果")
        scope = self._profile_key or "全部产品"
        self.scope_label.setText(f"当前筛选：{scope}")
        sessions = self._repository.list_recent(
            limit=100,
            profile_key=self._profile_key or None,
        )
        self.session_table.setRowCount(len(sessions))
        for row, session in enumerate(sessions):
            values = [
                session.started_at,
                session.robot_accid,
                session.status.value,
                str(session.pass_count),
                str(session.fail_count),
                str(session.not_applicable_count),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, session.session_id)
                self.session_table.setItem(row, column, item)

    def _load_selected(self):
        row = self.session_table.currentRow()
        if row < 0:
            return
        item = self.session_table.item(row, 0)
        session_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        session = self._repository.get(str(session_id)) if session_id else None
        self._selected_session = session
        if not session:
            self.result_table.setRowCount(0)
            self.rerun_failed_button.setEnabled(False)
            return

        self.detail_label.setText(
            f"会话 {session.session_id} · {session.operator_name} · "
            f"版本 {session.software_version}"
        )
        self.result_table.setRowCount(len(session.items))
        for result_row, result in enumerate(session.items):
            values = [
                result.category,
                result.name,
                result.status.value,
                result.summary,
                result.executed_at,
                result.note,
            ]
            for column, value in enumerate(values):
                self.result_table.setItem(
                    result_row,
                    column,
                    QTableWidgetItem(value),
                )
        self.rerun_failed_button.setEnabled(any(
            item.status == AcceptanceItemStatus.FAIL for item in session.items
        ))

    def _rerun_failed(self):
        if not self._selected_session:
            return
        failed_keys = [
            item.check_key
            for item in self._selected_session.items
            if item.status == AcceptanceItemStatus.FAIL
        ]
        if failed_keys:
            self.rerun_requested.emit(failed_keys)
