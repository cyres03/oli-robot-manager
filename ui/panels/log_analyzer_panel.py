"""Embedded robot log analyzer for after-sales acceptance work."""
import re
from dataclasses import dataclass

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


SLAVE_MOTOR_MAP = {
    2: (15, "waist pitch", False), 3: (14, "waist roll", False), 4: (13, "waist yaw", True),
    5: (18, "Left Shoulder pitch", False), 6: (19, "Left Shoulder roll", False),
    7: (20, "Left Shoulder yaw", False), 8: (21, "Left Elbow", False),
    9: (22, "Left Wrist yaw", False), 10: (23, "Left Wrist roll", False), 11: (24, "Left Wrist pitch", True),
    13: (1, "Left Hip pitch", False), 14: (2, "Left Hip roll", False), 15: (3, "Left Hip yaw", False),
    16: (4, "Left Knee", False), 17: (5, "Left Ankle pitch", False), 18: (6, "Left Ankle roll", True),
    19: (16, "Head yaw", False), 20: (17, "Head pitch", True),
    22: (25, "Right Shoulder pitch", False), 23: (26, "Right Shoulder roll", False),
    24: (27, "Right Shoulder yaw", False), 25: (28, "Right Elbow", False),
    26: (29, "Right Wrist yaw", False), 27: (30, "Right Wrist roll", False), 28: (31, "Right Wrist pitch", True),
    29: (7, "Right Hip pitch", False), 30: (8, "Right Hip roll", False), 31: (9, "Right Hip yaw", False),
    32: (10, "Right Knee", False), 33: (11, "Right Ankle pitch", False), 34: (12, "Right Ankle roll", True),
}

CONTROLLER_STATE_MAP = {
    "ZeroTorque": "零力矩", "MotionLibrary": "动作库", "Mimic": "舞蹈",
    "Walk": "拟人行走", "Damping": "阻尼", "IkStand": "站立",
    "IkStand,GroundDetection": "站立和离地检测", "GroundDetection": "离地检测",
    "LieDown": "躺着", "SitDown": "装箱姿势", "StandSit": "坐姿",
    "MotionEdit": "动作编排", "SitStand": "坐姿起身", "LieSit": "躺姿起身",
    "TeleopArmInit": "遥操作初始化", "TeleopArmInit,LBWalk": "遥操作初始化",
    "LBWalk,TeleopArmExit": "遥操作初始化退出姿态", "LBWalk": "LBWalk", "": "无控制器（校零模式）",
}


@dataclass
class LogEvent:
    category: str
    title: str
    detail: str
    timestamp: str
    line_number: int
    severity: str = "info"


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
            "QPlainTextEdit, QTreeWidget { background: #111827; color: #E5E7EB; border: 1px solid #374151; border-radius: 8px; font-family: Consolas, 'Courier New', monospace; font-size: 14px; }"
            "QTreeWidget::item { padding: 4px 6px; }"
            "QHeaderView::section { background: #1F2937; color: #E5E7EB; border: none; padding: 7px 8px; font-weight: 700; }"
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
            ("motor", "驱动器版本"), ("controller", "当前控制器"), ("faults", "通讯异常"),
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

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.setMinimumWidth(560)
        self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        splitter.addWidget(self.log_view)

        self.timeline = QTreeWidget()
        self.timeline.setHeaderLabels(["行", "类型", "事件", "详情"])
        self.timeline.setRootIsDecorated(False)
        self.timeline.setAlternatingRowColors(True)
        self.timeline.setMinimumWidth(330)
        self.timeline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.timeline.header().setStretchLastSection(True)
        self.timeline.itemClicked.connect(self._on_event_clicked)
        splitter.addWidget(self.timeline)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([760, 360])
        layout.addWidget(splitter, 1)

    def _open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择机器人日志", "", "日志文件 (*.log *.txt);;所有文件 (*.*)")
        if not file_path:
            return
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file_handle:
            self.analyze_text(file_handle.read(), file_path)

    def analyze_text(self, content: str, file_path: str = ""):
        self._original_lines = content.splitlines()
        self._events.clear()
        self.file_label.setText(file_path or "已加载日志")

        versions = {"pms": "-", "ecm": "-", "ctrl": "-", "motor": "-"}
        current_controller = "-"
        switch_count = 0
        fault_count = 0
        processed_faults = set()
        warned_motors = set()
        current_timestamp = "--:--:--"

        display_lines = []
        for line_index, line_text in enumerate(self._original_lines, start=1):
            display_lines.append(f"{line_index:>6}  {line_text}")
            timestamp_match = re.search(r"\[(\d{4}[-/]\d{2}[-/]\d{2}\s+?\d{2}:\d{2}:\d{2}(?:\.\d{3})?)\]", line_text) or re.search(r"(\d{2}:\d{2}:\d{2}(?:\.\d{3})?)", line_text)
            if timestamp_match:
                current_timestamp = timestamp_match.group(1)

            if "name:pms_version" in line_text:
                version_match = re.search(r"msg:([\d.]+)", line_text)
                if version_match:
                    versions["pms"] = version_match.group(1)
            elif "name:ecm_version" in line_text:
                version_match = re.search(r"msg:([\d.]+)", line_text)
                if version_match:
                    versions["ecm"] = version_match.group(1)
            elif "robot-hu-r-" in line_text:
                version_match = re.search(r"robot-hu-r-([\d.]+)", line_text)
                if version_match:
                    versions["ctrl"] = version_match.group(1)

            link_match = re.search(r"slave\s*=\s*(\d+).*?link_status\s*=\s*(0x[0-9a-fA-F]+)", line_text)
            if link_match:
                slave_id = int(link_match.group(1))
                status = link_match.group(2).lower()
                motor_info = SLAVE_MOTOR_MAP.get(slave_id)
                if motor_info and status != "0x5a37":
                    motor_id, part_name, is_last = motor_info
                    if not (status == "0x5617" and is_last):
                        fault_key = f"{slave_id}-{status}"
                        if fault_key not in processed_faults:
                            processed_faults.add(fault_key)
                            fault_count += 1
                            self._events.append(LogEvent(
                                "通讯", f"Slave{slave_id} 通讯异常", f"Motor{motor_id} {part_name} status={status}",
                                current_timestamp, line_index, "error" if status == "0x0" else "warning",
                            ))

            motor_warning = re.search(r"code:\s*65537.*?motor\s+(\d+)\s+MOTOR_WARNING.*?VOICE_PROMPT", line_text)
            if motor_warning:
                motor_id = int(motor_warning.group(1))
                if motor_id not in warned_motors:
                    warned_motors.add(motor_id)
                    self._events.append(LogEvent("电机", f"电机{motor_id} 语音警告", "可能堵转或过速", current_timestamp, line_index, "warning"))

            if "ability_running" in line_text and "message:" in line_text:
                controller_match = re.search(r"message:\s*([^)]+)", line_text)
                if controller_match:
                    state = CONTROLLER_STATE_MAP.get(controller_match.group(1).strip(), controller_match.group(1).strip())
                    if state != current_controller:
                        switch_count += 1
                        self._events.append(LogEvent("控制器", state, f"从 {current_controller}" if current_controller != "-" else "初始化", current_timestamp, line_index, "info"))
                        current_controller = state

            if re.search(r"recv\s+power\s+mtv\s+state\s*:\s*off", line_text, re.IGNORECASE):
                self._events.append(LogEvent("电源", "驱动器下电", "所有关节电机断电", current_timestamp, line_index, "warning"))
            elif re.search(r"recv\s+power\s+mtv\s+state\s*:\s*on", line_text, re.IGNORECASE):
                self._events.append(LogEvent("电源", "驱动器上电", "电机预充电完成", current_timestamp, line_index, "success"))

        self.log_view.setPlainText("\n".join(display_lines))
        self.summary_labels["pms"].setText(versions["pms"])
        self.summary_labels["ecm"].setText(versions["ecm"])
        self.summary_labels["ctrl"].setText(versions["ctrl"])
        self.summary_labels["motor"].setText(versions["motor"])
        self.summary_labels["controller"].setText(current_controller)
        self.summary_labels["faults"].setText(f"{fault_count} 个异常 / {switch_count} 次切换")
        self._render_timeline()

    def _render_timeline(self):
        self.timeline.clear()
        for event in sorted(self._events, key=lambda item: item.line_number):
            item = QTreeWidgetItem([str(event.line_number), event.category, event.title, event.detail])
            item.setData(0, Qt.ItemDataRole.UserRole, event.line_number)
            self.timeline.addTopLevelItem(item)
        self.timeline.setColumnWidth(0, 80)
        self.timeline.setColumnWidth(1, 90)
        self.timeline.setColumnWidth(2, 220)
        self.timeline.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

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