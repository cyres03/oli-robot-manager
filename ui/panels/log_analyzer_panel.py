"""Embedded robot log analyzer for after-sales acceptance work."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from services.log_analysis import LogEvent, analyze_log


class LogAnalyzerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_lines: list[str] = []
        self._events: list[LogEvent] = []
        self._search_matches: list[int] = []
        self._current_match = -1
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(
            "QWidget#logAnalyzer { background: #F8F9FA; }"
            "QLabel#sectionTitle { font-size: 15px; font-weight: 700; color: #1D2129; background: transparent; }"
            "QLabel[cssClass='cardLabel'] { color: #86909C; font-size: 11px; background: transparent; }"
            "QLabel[cssClass='cardValue'] { color: #1D2129; font-size: 13px; font-weight: 700; background: transparent; }"
            "QPushButton { background: #FFFFFF; color: #1D2129; border: 1px solid #E5E6EB; border-radius: 6px; padding: 8px 14px; }"
            "QPushButton:hover { background: #F2F3F5; border-color: #C9CDD4; }"
            "QLineEdit { background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; padding: 8px 10px; }"
            "QPlainTextEdit#logContent { background: #111827; color: #E5E7EB; border: 1px solid #374151; border-radius: 8px; font-family: Consolas, 'Courier New', monospace; font-size: 14px; }"
            "QTreeWidget#eventTimeline { background: #FFFFFF; alternate-background-color: #F6F9FF; color: #263550; border: 1px solid #D7E3F4; border-radius: 8px; font-size: 13px; outline: none; }"
            "QTreeWidget#eventTimeline::item { color: #263550; padding: 6px 7px; border-bottom: 1px solid #EDF2FA; }"
            "QTreeWidget#eventTimeline::item:selected { background: #E8F0FF; color: #1D2A44; }"
            "QTreeWidget#eventTimeline QHeaderView::section { background: #EAF1FF; color: #34518D; border: none; border-right: 1px solid #D7E3F4; border-bottom: 1px solid #D7E3F4; padding: 7px 6px; font-weight: 700; }"
        )
        self.setObjectName("logAnalyzer")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        title = QLabel("机器人日志分析")
        title.setObjectName("sectionTitle")
        top_bar.addWidget(title)
        top_bar.addStretch()

        self.file_label = QLabel("未选择日志")
        self.file_label.setStyleSheet("color: #86909C; background: transparent;")
        top_bar.addWidget(self.file_label)

        open_btn = QPushButton("导入日志")
        open_btn.clicked.connect(self._open_file)
        top_bar.addWidget(open_btn)
        layout.addLayout(top_bar)

        cards = QGridLayout()
        cards.setHorizontalSpacing(8)
        cards.setVerticalSpacing(8)
        self.summary_labels = {}
        for index, (key, label) in enumerate([
            ("pms", "分电板版本"), ("ecm", "主站版本"), ("ctrl", "主控软件版本"),
            ("motor", "驱动器版本"), ("controller", "当前控制器"), ("faults", "诊断发现"),
        ]):
            card = QWidget()
            card.setStyleSheet("background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 8px;")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            label_widget = QLabel(label)
            label_widget.setProperty("cssClass", "cardLabel")
            value_widget = QLabel("-")
            value_widget.setProperty("cssClass", "cardValue")
            card_layout.addWidget(label_widget)
            card_layout.addWidget(value_widget)
            self.summary_labels[key] = value_widget
            cards.addWidget(card, index // 3, index % 3)
        layout.addLayout(cards)

        search_bar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索日志关键词，回车跳到下一个")
        self.search_input.returnPressed.connect(lambda: self._navigate_search(1))
        self.search_input.textChanged.connect(self._perform_search)
        search_bar.addWidget(self.search_input)
        prev_btn = QPushButton("上一个")
        prev_btn.clicked.connect(lambda: self._navigate_search(-1))
        next_btn = QPushButton("下一个")
        next_btn.clicked.connect(lambda: self._navigate_search(1))
        self.search_info = QLabel("0/0")
        self.search_info.setStyleSheet("color: #86909C; background: transparent;")
        search_bar.addWidget(prev_btn)
        search_bar.addWidget(next_btn)
        search_bar.addWidget(self.search_info)
        layout.addLayout(search_bar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logContent")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.setMinimumWidth(440)
        self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.splitter.addWidget(self.log_view)

        self.timeline = QTreeWidget()
        self.timeline.setObjectName("eventTimeline")
        self.timeline.setHeaderLabels(["行", "类型", "事件"])
        self.timeline.setRootIsDecorated(False)
        self.timeline.setAlternatingRowColors(True)
        self.timeline.setUniformRowHeights(True)
        self.timeline.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline.setMinimumWidth(300)
        self.timeline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        header = self.timeline.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.timeline.setColumnWidth(0, 58)
        self.timeline.setColumnWidth(1, 72)
        self.timeline.itemClicked.connect(self._on_event_clicked)
        self.splitter.addWidget(self.timeline)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([680, 340])
        layout.addWidget(self.splitter, 1)

    def _open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择机器人日志", "", "日志文件 (*.log *.txt);;所有文件 (*.*)")
        if not file_path:
            return
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file_handle:
            self.analyze_text(file_handle.read(), file_path)

    def analyze_text(self, content: str, file_path: str = ""):
        self._original_lines = content.splitlines()
        analysis = analyze_log(content)
        self._events = list(analysis.events)
        file_name = file_path or "已加载日志"
        self.file_label.setText(f"{analysis.product_name} · {analysis.sn} · {file_name}")
        display_lines = [
            f"{line_index:>6}  {line_text}"
            for line_index, line_text in enumerate(self._original_lines, start=1)
        ]
        self.log_view.setPlainText("\n".join(display_lines))
        for key in ("pms", "ecm", "ctrl", "motor"):
            self.summary_labels[key].setText(analysis.versions[key])
        self.summary_labels["controller"].setText(analysis.current_controller)
        error_count = sum(
            finding.severity == "error" for finding in analysis.findings
        )
        warning_count = sum(
            finding.severity == "warning" for finding in analysis.findings
        )
        self.summary_labels["faults"].setText(
            f"{error_count} 错误 / {warning_count} 告警 / "
            f"{analysis.controller_switch_count} 次切换"
        )
        self._render_timeline()

    def _render_timeline(self):
        self.timeline.clear()
        for event in sorted(self._events, key=lambda item: item.line_number):
            event_text = event.title
            if event.detail:
                event_text = f"{event.title} · {event.detail}"
            item = QTreeWidgetItem([
                str(event.line_number),
                event.category,
                event_text,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, event.line_number)
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(2, event_text)
            self.timeline.addTopLevelItem(item)

    def _on_event_clicked(self, item: QTreeWidgetItem):
        line_number = int(item.data(0, Qt.ItemDataRole.UserRole) or 1)
        self._scroll_to_line(line_number)

    def _perform_search(self, keyword: str):
        self._search_matches = []
        self._current_match = -1
        if keyword:
            keyword_lower = keyword.lower()
            self._search_matches = [index for index, line_text in enumerate(self._original_lines, start=1) if keyword_lower in line_text.lower()]
            if self._search_matches:
                self._current_match = 0
                self._scroll_to_line(self._search_matches[0])
        self._update_search_info()

    def _navigate_search(self, direction: int):
        if not self._search_matches:
            return
        self._current_match = (self._current_match + direction) % len(self._search_matches)
        self._scroll_to_line(self._search_matches[self._current_match])
        self._update_search_info()

    def _update_search_info(self):
        if not self._search_matches:
            self.search_info.setText("0/0")
            return
        self.search_info.setText(f"{self._current_match + 1}/{len(self._search_matches)}")

    def _scroll_to_line(self, line_number: int):
        target_block = max(0, line_number - 1)
        document = self.log_view.document()
        block = document.findBlockByNumber(target_block)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        self.log_view.setTextCursor(cursor)
        self.log_view.centerCursor()