"""Card widget — dance or motion with name, badge, count, and execute button."""
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PyQt6.QtCore import pyqtSignal


class ExecuteButton(QPushButton):
    """Dedicated button class to avoid QSS selector conflicts."""


class DanceCard(QFrame):
    execute_clicked = pyqtSignal()
    repeat_clicked = pyqtSignal()

    def __init__(self, name: str, category: str = "dance", count: int = 0,
                 subtitle: str = "", repeat_enabled: bool = False,
                 executable: bool = True, unavailable_reason: str = "", parent=None):
        super().__init__(parent)
        self._name = name
        self._category = category
        self._count = count

        self.setObjectName("danceCard")
        self.setFixedSize(160, 120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # Category badge
        cat_lbl = QLabel(category.upper())
        cat_lbl.setStyleSheet("font-size: 9px; color: #86909C; letter-spacing: 1px; border: none; background: transparent;")
        layout.addWidget(cat_lbl)

        # Name
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #1D2129; border: none; background: transparent;")
        name_lbl.setWordWrap(True)
        layout.addWidget(name_lbl)

        # Subtitle + count badge
        info_row = QHBoxLayout()
        info_row.setSpacing(6)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet("font-size: 10px; color: #86909C; border: none; background: transparent;")
            info_row.addWidget(sub, 1)
        else:
            info_row.addStretch(1)
        self.count_badge = QLabel(str(count))
        self.count_badge.setObjectName("countBadge")
        self.count_badge.setFixedHeight(18)
        info_row.addWidget(self.count_badge)
        layout.addLayout(info_row)

        layout.addStretch()

        # Execute button — use direct property setting, no QSS selectors
        exec_btn = ExecuteButton("执行")
        exec_btn.clicked.connect(self.execute_clicked.emit)
        exec_btn.setEnabled(executable)
        if unavailable_reason:
            exec_btn.setToolTip(unavailable_reason)
        if repeat_enabled:
            button_row = QHBoxLayout()
            button_row.setSpacing(6)
            repeat_btn = ExecuteButton("5次")
            repeat_btn.setToolTip("连续执行 5 次：每次成功后等待 2 秒再继续")
            repeat_btn.clicked.connect(self.repeat_clicked.emit)
            repeat_btn.setEnabled(executable)
            if unavailable_reason:
                repeat_btn.setToolTip(unavailable_reason)
            button_row.addWidget(exec_btn)
            button_row.addWidget(repeat_btn)
            layout.addLayout(button_row)
        else:
            layout.addWidget(exec_btn)

    def set_count(self, count: int):
        self._count = count
        self.count_badge.setText(str(count))

    @property
    def dance_name(self) -> str:
        return self._name
