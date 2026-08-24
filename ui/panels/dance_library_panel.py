"""Dance & motion library — tabbed, compact layout."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QSlider, QTabWidget, QGridLayout, QFrame,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from models.robot_profile import RobotProfile
from services.dance_service import DanceService
from ui.widgets.dance_card import DanceCard
from ui.widgets.sequencer_editor import SequencerEditor


DANCE_DISPLAY_ORDER = [
    ("胜利之舞",),
    ("热烈",),
    ("低俗小说",),
    ("顺风顺水顺财神",),
    ("万物生",),
    ("机械舞",),
    ("相亲相爱",),
    ("APT",),
    ("扭胯舞", "abracadabr扭胯舞"),
    ("卡拉永远ok", "卡拉永远OK"),
    ("来个蹦蹦",),
    ("gentleman",),
    ("管他什么音乐",),
    ("孤身摇",),
]

UNRELIABLE_MOTIONS = {
    "raise_and_introduce": "该动作当前固件不返回完成通知，暂不参与自动验收",
}


def _normalize_dance_label(value: str) -> str:
    return "".join(value.lower().split())


def _dance_display_order_key(dance: dict, original_index: int) -> tuple[int, int]:
    labels = [
        _normalize_dance_label(str(dance.get("name", ""))),
        _normalize_dance_label(str(dance.get("english_name", ""))),
        _normalize_dance_label(str(dance.get("rc_mapping", ""))),
    ]
    for order, aliases in enumerate(DANCE_DISPLAY_ORDER):
        normalized_aliases = [_normalize_dance_label(alias) for alias in aliases]
        if any(alias and any(alias in label for label in labels) for alias in normalized_aliases):
            return (order, original_index)
    return (len(DANCE_DISPLAY_ORDER), original_index)


class FlowGrid(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setSpacing(8)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._count = 0
        self._cols = 5

    def add_card(self, card: DanceCard):
        row = self._count // self._cols
        col = self._count % self._cols
        self._layout.addWidget(card, row, col)
        self._count += 1

    def clear_cards(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._count = 0


class DanceLibraryPanel(QWidget):
    def __init__(self, dance_service: DanceService, parent=None):
        super().__init__(parent)
        self._service = dance_service
        self._allowed_tools: frozenset[str] | None = None
        self._dance_cards: dict[str, DanceCard] = {}
        self._motion_cards: dict[str, DanceCard] = {}
        self._walk_timer: QTimer | None = None
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # Toolbar
        bar = QHBoxLayout()
        self.refresh_dances_btn = QPushButton("刷新舞蹈")
        self.refresh_motions_btn = QPushButton("刷新动作")
        self.motion_engine_btn = QPushButton("手动动作库模式")
        self.stop_repeat_btn = QPushButton("停止连续动作")
        self.stop_repeat_btn.setEnabled(False)
        self.motion_engine_btn.setCheckable(True)
        for b in [self.refresh_dances_btn, self.refresh_motions_btn, self.motion_engine_btn, self.stop_repeat_btn]:
            b.setFixedHeight(30)
            b.setStyleSheet(
                "QPushButton { background: #FFFFFF; color: #1D2129; border: 1px solid #E5E6EB; "
                "border-radius: 6px; padding: 4px 14px; font-size: 12px; }"
                "QPushButton:hover { border-color: #6C5CE7; color: #6C5CE7; }"
                "QPushButton:checked { background: #6C5CE7; color: #fff; border-color: #6C5CE7; }")
        bar.addWidget(self.refresh_dances_btn)
        bar.addWidget(self.refresh_motions_btn)
        bar.addWidget(self.motion_engine_btn)
        bar.addWidget(self.stop_repeat_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self.action_status_label = QLabel("执行舞蹈/动作时会自动进入动作库模式，结束后自动回拟人行走模式")
        self.action_status_label.setStyleSheet(
            "color: #4E5969; font-size: 12px; padding: 2px 0; border: none; background: transparent;"
        )
        layout.addWidget(self.action_status_label)

        # Tabs: Dances | Motions | Walk | Sequencer
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #E5E6EB; background: #FFFFFF; border-radius: 8px; }
            QTabBar::tab { background: #F2F3F5; color: #86909C; padding: 8px 18px; border: none; font-size: 12px; margin-right: 2px; }
            QTabBar::tab:selected { background: #FFFFFF; color: #6C5CE7; border-bottom: 2px solid #6C5CE7; }
            QTabBar::tab:hover:!selected { color: #4E5969; }
        """)

        # Tab 1: Dances
        self.dance_grid = FlowGrid()
        dance_scroll = QScrollArea()
        dance_scroll.setWidgetResizable(True)
        dance_scroll.setWidget(self.dance_grid)
        dance_scroll.setStyleSheet("QScrollArea { border: none; }")
        self.tabs.addTab(dance_scroll, "舞蹈 (Dances)")

        # Tab 2: Motions
        self.motion_grid = FlowGrid()
        motion_scroll = QScrollArea()
        motion_scroll.setWidgetResizable(True)
        motion_scroll.setWidget(self.motion_grid)
        motion_scroll.setStyleSheet("QScrollArea { border: none; }")
        self.tabs.addTab(motion_scroll, "动作 (Motions)")

        # Tab 3: Walking
        walk_tab = QWidget()
        walk_layout = QVBoxLayout(walk_tab)
        walk_layout.setContentsMargins(16, 16, 16, 16)
        for name, attr in [("前后 vx", "vx"), ("横向 vy", "vy"), ("旋转 yaw", "yaw")]:
            row = QHBoxLayout()
            lbl = QLabel(f"{name}: 0.00")
            lbl.setStyleSheet("color: #4E5969; min-width: 80px; font-size: 12px; border: none; background: transparent;")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(-100, 100)
            slider.setValue(0)
            slider.valueChanged.connect(
                lambda v, l=lbl, n=name: l.setText(f"{n}: {v / 100:.2f}"))
            setattr(self, f"slider_{attr}", slider)
            row.addWidget(lbl)
            row.addWidget(slider)
            walk_layout.addLayout(row)
        self.apply_walk_btn = QPushButton("应用速度")
        self.apply_walk_btn.setStyleSheet(
            "QPushButton { background: #6C5CE7; color: #fff; border: none; "
            "border-radius: 6px; padding: 8px; font-weight: 700; }"
            "QPushButton:hover { background: #5A4BD1; }")
        self.apply_walk_btn.clicked.connect(self._apply_walk)
        walk_layout.addWidget(self.apply_walk_btn)
        self.walk_status_label = QLabel("非零速度会持续发送；三轴归零后点击应用速度可停止")
        self.walk_status_label.setStyleSheet(
            "color: #86909C; font-size: 12px; border: none; background: transparent;"
        )
        walk_layout.addWidget(self.walk_status_label)
        self._walk_timer = QTimer(self)
        self._walk_timer.setInterval(200)
        self._walk_timer.timeout.connect(self._send_walk_velocity_once)
        walk_layout.addStretch()
        self.tabs.addTab(walk_tab, "行走")

        # Tab 4: Sequencer
        seq_tab = QWidget()
        seq_layout = QVBoxLayout(seq_tab)
        seq_layout.setContentsMargins(8, 8, 8, 8)
        self.sequencer = SequencerEditor()
        seq_layout.addWidget(self.sequencer)
        self.tabs.addTab(seq_tab, "序列器")

        layout.addWidget(self.tabs)

    def _connect_signals(self):
        self.refresh_dances_btn.clicked.connect(self._service.load_dances)
        self.refresh_motions_btn.clicked.connect(self._service.load_motions)
        self.motion_engine_btn.clicked.connect(self._on_motion_engine_toggled)
        self.stop_repeat_btn.clicked.connect(self._service.stop_motion_repeat)
        self._service.dance_list_loaded.connect(self._populate_dances)
        self._service.motion_list_loaded.connect(self._populate_motions)
        self._service.dance_executed.connect(self._on_dance_executed)
        self._service.dance_target_completed.connect(self._on_dance_target_completed)
        self._service.motion_executed.connect(self._on_motion_executed)
        self._service.action_state_changed.connect(self._on_action_state_changed)
        self._service.motion_engine_changed.connect(self.motion_engine_btn.setChecked)
        self.sequencer.execute_sequence_clicked.connect(self._on_execute_sequence)
        self.sequencer.save_sequence_clicked.connect(self._service.save_sequence)

    def _populate_dances(self, dances: list[dict]):
        self.dance_grid.clear_cards()
        self._dance_cards.clear()
        names = []
        ordered_dances = [
            dance for _, dance in sorted(
                enumerate(dances),
                key=lambda item: _dance_display_order_key(item[1], item[0]),
            )
        ]
        for d in ordered_dances:
            rc = d.get("rc_mapping", "")
            cn = d.get("name", "") or d.get("english_name", rc)
            en = d.get("english_name", "")
            dur = d.get("duration", 0)
            count = self._service.get_count(rc)
            card = DanceCard(cn, "dance", count, subtitle=f"{en} · {dur}s" if en else "")
            card.execute_clicked.connect(lambda n=rc: self._service.execute_dance(n))
            card.setEnabled(self._tool_allowed("execute_dance"))
            self.dance_grid.add_card(card)
            self._dance_cards[rc] = card
            names.append(rc)
        self.sequencer.set_dance_names(names)

    def _populate_motions(self, motions: list[dict]):
        self.motion_grid.clear_cards()
        self._motion_cards.clear()
        for m in motions:
            en = m.get("motion_name_en", "")
            cn = m.get("motion_name_cn", "")
            count = self._service.get_count(en)
            unavailable_reason = UNRELIABLE_MOTIONS.get(en, "")
            subtitle = unavailable_reason if unavailable_reason else (en if cn else "")
            card = DanceCard(
                cn or en,
                "motion",
                count,
                subtitle=subtitle,
                repeat_enabled=True,
                executable=not unavailable_reason,
                unavailable_reason=unavailable_reason,
            )
            if not unavailable_reason:
                card.execute_clicked.connect(lambda n=en: self._service.execute_motion(n))
                card.repeat_clicked.connect(lambda n=en: self._service.execute_motion_repeat(n, times=5, delay_ms=2000))
            card.setEnabled(self._tool_allowed("execute_motion") and not unavailable_reason)
            self.motion_grid.add_card(card)
            self._motion_cards[en] = card

    def _on_dance_executed(self, name: str, count: int):
        if name in self._dance_cards:
            self._dance_cards[name].set_count(count)

    def _on_dance_target_completed(self, name: str, count: int, robot_accid: str):
        display_name = self._dance_cards[name].dance_name if name in self._dance_cards else name
        QMessageBox.information(
            self,
            "舞蹈测试完成",
            f"{robot_accid}\n{display_name} 已测试到第 {count} 遍。",
        )

    def _on_motion_executed(self, name: str, count: int):
        if name in self._motion_cards:
            self._motion_cards[name].set_count(count)

    def _on_action_state_changed(self, running: bool, label: str):
        if running:
            self.stop_continuous_walk(reset_sliders=True, send_stop=True)
        self.action_status_label.setText(label)
        repeat_running = label.startswith("连续动作")
        for card in self._dance_cards.values():
            card.setEnabled(not running and self._tool_allowed("execute_dance"))
        for name, card in self._motion_cards.items():
            card.setEnabled(
                not running
                and self._tool_allowed("execute_motion")
                and name not in UNRELIABLE_MOTIONS
            )
        self.refresh_dances_btn.setEnabled(not running and self._tool_allowed("get_dances"))
        self.refresh_motions_btn.setEnabled(not running and self._tool_allowed("get_motions"))
        self.motion_engine_btn.setEnabled(not running and self._tool_allowed("set_motion_engine"))
        self.sequencer.setEnabled(not running and self._tool_allowed("execute_motion"))
        self.stop_repeat_btn.setEnabled(repeat_running and running)

    def apply_profile(self, profile: RobotProfile | None):
        self._allowed_tools = profile.allowed_tools if profile else frozenset()
        if not self._tool_allowed("set_walk_velocity"):
            self.stop_continuous_walk(reset_sliders=True, send_stop=False)
        self.refresh_dances_btn.setEnabled(self._tool_allowed("get_dances"))
        self.refresh_motions_btn.setEnabled(self._tool_allowed("get_motions"))
        self.motion_engine_btn.setEnabled(self._tool_allowed("set_motion_engine"))
        self.apply_walk_btn.setEnabled(self._tool_allowed("set_walk_velocity"))
        self.tabs.setTabEnabled(2, self._tool_allowed("set_walk_velocity"))
        self.tabs.setTabEnabled(3, self._tool_allowed("execute_motion"))
        self.sequencer.setEnabled(self._tool_allowed("execute_motion"))
        for card in self._dance_cards.values():
            card.setEnabled(self._tool_allowed("execute_dance"))
        for name, card in self._motion_cards.items():
            card.setEnabled(
                self._tool_allowed("execute_motion") and name not in UNRELIABLE_MOTIONS
            )
        if profile and not self._tool_allowed("execute_motion"):
            self.action_status_label.setText(
                f"{profile.display_name} 当前仅开放动作与舞蹈列表查询"
            )

    def _tool_allowed(self, tool_name: str) -> bool:
        return self._allowed_tools is None or tool_name in self._allowed_tools

    def _apply_walk(self):
        vx = self.slider_vx.value() / 100.0
        vy = self.slider_vy.value() / 100.0
        yaw = self.slider_yaw.value() / 100.0

        self._send_walk_velocity_once()
        if vx == 0 and vy == 0 and yaw == 0:
            if self._walk_timer and self._walk_timer.isActive():
                self._walk_timer.stop()
            self.apply_walk_btn.setText("应用速度")
            self.walk_status_label.setText("已发送停止速度")
            return

        if self._walk_timer and not self._walk_timer.isActive():
            self._walk_timer.start()
        self.apply_walk_btn.setText("更新持续速度")
        self.walk_status_label.setText(f"持续发送: vx={vx:.2f}, vy={vy:.2f}, yaw={yaw:.2f}")

    def _send_walk_velocity_once(self):
        self._service.set_walk_velocity(
            self.slider_vx.value() / 100.0,
            self.slider_vy.value() / 100.0,
            self.slider_yaw.value() / 100.0,
        )

    def stop_continuous_walk(self, reset_sliders: bool = True, send_stop: bool = True):
        if self._walk_timer and self._walk_timer.isActive():
            self._walk_timer.stop()
        if reset_sliders:
            self.slider_vx.setValue(0)
            self.slider_vy.setValue(0)
            self.slider_yaw.setValue(0)
        if send_stop and self._tool_allowed("set_walk_velocity"):
            self._service.set_walk_velocity(0.0, 0.0, 0.0)
        self.apply_walk_btn.setText("应用速度")
        self.walk_status_label.setText("持续行走已停止")

    def _on_motion_engine_toggled(self, checked: bool):
        self.stop_continuous_walk(reset_sliders=True, send_stop=True)
        self._service.set_motion_engine(1 if checked else 0)

    def _on_execute_sequence(self, sequence):
        self.stop_continuous_walk(reset_sliders=True, send_stop=True)
        self._service.execute_sequence(sequence)
