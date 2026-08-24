"""Robot basic control panel — state-aware one-click actions."""
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QGroupBox, QGridLayout, QTextEdit, QScrollArea,
    QCheckBox, QHBoxLayout, QSpinBox, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, QTimer, Qt
from PyQt6.QtWidgets import QSizePolicy
from models.robot_profile import RobotProfile
from workers.mcp_worker import McpWorker


class ControlButton(QPushButton):
    """Dedicated button class for control panel — avoids QSS selector conflicts."""


# Modes that allow walking
WALK_ALLOWED_MODES = {"damping", "set_walk_mode"}

# Modes that disable walking
WALK_DISABLED_MODES = {"zero_torque", "prepare", "standup", "sit_down", "lie_down",
                         "set_motion_engine_1", "set_motion_engine"}


class ControlPanel(QWidget):
    action_requested = pyqtSignal(str, dict)

    GROUPS = [
        ("姿态控制", [
            ("prepare", "准备站立", "进入准备姿态"),
            ("standup", "站立", "从躺/坐/吊状态站起"),
            ("sit_down", "坐下", "从站立到坐下"),
            ("lie_down", "躺下", "从站立到躺下"),
        ]),
        ("模式切换", [
            ("safe_stop", "停止行走", "仅发送 x/y/yaw=0，不进入阻尼/零力矩"),
            ("set_walk_mode", "行走模式", "需先在阻尼状态下才可进入"),
            ("set_motion_engine_1", "动作库模式", "进入动作库引擎"),
            ("damping", "阻尼模式", "电机阻尼感"),
            ("zero_torque", "零力矩", "电机无力矩"),
        ]),
        ("测试", [
            ("straight_walk", "直线行走测试", "自动检测机器名称, 0.7m/s持续10秒后停止"),
        ]),
        ("状态与音频", [
            ("get_action_library_status", "动作库状态", "查询动作库模式与运行状态"),
            ("audio_get_wakeup", "查询唤醒词", "获取当前唤醒词设置"),
            ("audio_wakeup_enable", "开启唤醒", "开启唤醒词检测"),
            ("audio_wakeup_disable", "关闭唤醒", "关闭唤醒词检测"),
        ]),
        ("灯效", [
            ("led_green", "绿灯常亮", "全部 LED 绿色常亮"),
            ("led_breathe_blue", "蓝灯呼吸", "全部 LED 蓝色呼吸"),
            ("led_off", "关闭灯效", "关闭全部 LED 灯效"),
        ]),
    ]

    BUTTON_TOOLS = {
        "prepare": "prepare",
        "standup": "standup",
        "sit_down": "sit_down",
        "lie_down": "lie_down",
        "safe_stop": "safe_stop",
        "set_walk_mode": "set_walk_mode",
        "set_motion_engine_1": "set_motion_engine",
        "damping": "damping",
        "zero_torque": "zero_torque",
        "straight_walk": "set_walk_velocity",
        "get_action_library_status": "get_action_library_status",
        "audio_get_wakeup": "audio_get_wakeup",
        "audio_wakeup_enable": "audio_wakeup_control",
        "audio_wakeup_disable": "audio_wakeup_control",
        "led_green": "led_control",
        "led_breathe_blue": "led_control",
        "led_off": "led_control",
    }

    def __init__(self, mcp_worker: McpWorker, parent=None):
        super().__init__(parent)
        self._mcp = mcp_worker
        self._allowed_tools: frozenset[str] | None = None
        self._current_mode = "unknown"
        self._robot_status = "unknown"
        self._last_robot_status_key = None
        self._tool_buttons: dict[str, QPushButton] = {}
        self._posture_cycle_timer: QTimer | None = None
        self._posture_cycle_active = False
        self._posture_cycle_kind = ""
        self._posture_cycle_step = 0
        self._posture_cycle_total_steps = 0
        self._posture_cycle_count = 5
        self._posture_cycle_waiting_for = ""
        self._walk_timer: QTimer | None = None
        self._build_ui()
        self._mcp.tool_result_ready.connect(self._on_result)
        self._mcp.tool_error.connect(self._on_tool_error)
        self._update_button_states()

    def _on_result(self, tool_name: str, result):
        handled_tools = (
            {
                "audio_get_wakeup",
                "get_action_library_status",
                "audio_wakeup_control",
                "enable_led_control",
                "led_control",
                "audio_set_volume",
                "safe_stop",
                "standup",
            }
            | WALK_ALLOWED_MODES
            | WALK_DISABLED_MODES
        )
        is_waiting_posture_result = self._posture_cycle_active and tool_name == self._posture_cycle_waiting_for
        if tool_name not in handled_tools and not is_waiting_posture_result:
            return

        content = result.get("content", [])
        data = {}
        if content and isinstance(content[0], str):
            try:
                data = json.loads(content[0])
            except json.JSONDecodeError:
                data = {"raw": content[0]}

        if tool_name == "audio_get_wakeup":
            lines = []
            for k in ("word", "pinyin", "thresh", "greeting", "subsets", "backend"):
                if k in data:
                    lines.append(f"  {k}: {data[k]}")
            self.result_display.setText("唤醒词信息:\n" + "\n".join(lines) if lines else "未返回唤醒词信息")

        elif tool_name == "get_action_library_status":
            lines = [
                f"result: {data.get('result', '?')}",
                f"action_library_mode: {data.get('action_library_mode', '?')}",
                f"action_library_state: {data.get('action_library_state', '?')}",
            ]
            self.result_display.setText("动作库状态:\n" + "\n".join(lines))

        elif tool_name in {"audio_wakeup_control", "enable_led_control", "led_control", "audio_set_volume", "safe_stop"}:
            verb_map = {
                "audio_wakeup_control": "唤醒检测",
                "enable_led_control": "灯效控制",
                "led_control": "灯效设置",
                "audio_set_volume": "音量设置",
                "safe_stop": "停止行走",
            }
            result_text = data.get("result", data.get("stop_walk", data.get("damping", "unknown")))
            self.result_display.setText(f"{verb_map.get(tool_name, tool_name)}: {result_text}")

        if self._posture_cycle_active and tool_name == self._posture_cycle_waiting_for:
            self._handle_posture_cycle_result(tool_name, result, data)

        # Track mode changes
        if tool_name in WALK_ALLOWED_MODES and result.get("success"):
            self._current_mode = tool_name
            self._update_button_states()
        elif tool_name == "standup" and result.get("success") and data.get("set_walk_mode") == "success":
            self._current_mode = "set_walk_mode"
            self._update_button_states()
        elif tool_name in WALK_DISABLED_MODES and result.get("success"):
            self._current_mode = "set_motion_engine_1" if tool_name == "set_motion_engine" else tool_name
            self._update_button_states()

    def _on_tool_error(self, tool_name: str, message: str):
        if self._posture_cycle_active and tool_name == self._posture_cycle_waiting_for:
            self._stop_posture_cycle(f"姿态循环停止：{tool_name} 执行错误 {message}")

    def _build_ui(self):
        self.setObjectName("controlPanel")
        self.setStyleSheet(
            "QWidget#controlPanel { background: #F8F9FA; }"
            "QScrollArea#controlScroll { border: none; background: transparent; }"
            "QWidget#controlContent { background: transparent; }"
            "QLabel#controlTitle { font-size: 20px; font-weight: 700; color: #1D2129; border: none; background: transparent; }"
            "QLabel#controlDesc { color: #86909C; font-size: 13px; border: none; background: transparent; }"
            "QLabel#controlHint { color: #86909C; font-size: 12px; padding: 4px 0; border: none; background: transparent; }"
            "QGroupBox#controlGroup { color: #1D2129; font-size: 14px; font-weight: 700;"
            " border: 1px solid #E5E6EB; border-radius: 10px; margin-top: 12px;"
            " padding: 24px 16px 16px 16px; background: #FFFFFF; }"
            "QGroupBox#controlGroup::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #1D2129; }"
            "ControlButton { background: #F7F8FA; color: #1D2129; border: 1px solid #E5E6EB;"
            " border-radius: 6px; padding: 8px 12px; font-size: 13px; font-weight: 500; }"
            "ControlButton:hover { background: #EEF2F6; border-color: #CBD5E1; color: #334155; }"
            "ControlButton:pressed { background: #E5E7EB; border-color: #CBD5E1; }"
            "ControlButton:disabled { background: #F2F3F5; color: #C9CDD4; border-color: #E5E6EB; }"
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("controlScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root_layout.addWidget(scroll)

        content = QWidget()
        content.setObjectName("controlContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("机器人基础控制")
        title.setObjectName("controlTitle")
        layout.addWidget(title)

        desc = QLabel("一键快捷操作，控制机器人姿态和模式")
        desc.setObjectName("controlDesc")
        layout.addWidget(desc)

        self.hanging_guard = QCheckBox("吊装保护")
        self.hanging_guard.setChecked(True)
        self.hanging_guard.setToolTip("吊装时禁止坐下、躺下和直线行走测试；动作库/舞蹈/动作允许执行；站立使用 hanging 模式")
        self.hanging_guard.setStyleSheet(
            "QCheckBox { color: #1D2129; font-size: 13px; font-weight: 600; padding: 6px 0; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        self.hanging_guard.toggled.connect(self._update_button_states)
        layout.addWidget(self.hanging_guard)

        self.damping_guard = QCheckBox("允许阻尼/零力矩按钮")
        self.damping_guard.setChecked(False)
        self.damping_guard.setToolTip("SDK 中阻尼/零力矩会让全身停止主动运动；默认锁定，只能手动解锁后点击")
        self.damping_guard.setStyleSheet(
            "QCheckBox { color: #B42318; font-size: 13px; font-weight: 700; padding: 6px 0; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        self.damping_guard.toggled.connect(self._update_button_states)
        layout.addWidget(self.damping_guard)

        for group_name, buttons in self.GROUPS:
            grp = QGroupBox(group_name)
            grp.setObjectName("controlGroup")
            grp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            grid = QGridLayout(grp)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(12)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)

            for i, (tool, label, tip) in enumerate(buttons):
                btn = ControlButton(label)
                btn.setToolTip(tip)
                btn.setMinimumHeight(44)
                btn.setMinimumWidth(140)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.clicked.connect(lambda checked, t=tool, l=label: self._execute(t, l))
                if len(buttons) == 1:
                    grid.addWidget(btn, 0, 0, 1, 2)
                else:
                    grid.addWidget(btn, i // 2, i % 2)
                self._tool_buttons[tool] = btn

            layout.addWidget(grp)

        cycle_grp = QGroupBox("姿态循环验收")
        cycle_grp.setObjectName("controlGroup")
        cycle_grp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        cycle_layout = QVBoxLayout(cycle_grp)
        cycle_layout.setContentsMargins(8, 8, 8, 8)
        cycle_layout.setSpacing(10)

        interval_row = QHBoxLayout()
        sit_interval_label = QLabel("坐下起身间隔")
        sit_interval_label.setObjectName("controlHint")
        self.sit_posture_interval_spin = QSpinBox()
        self.sit_posture_interval_spin.setRange(5, 60)
        self.sit_posture_interval_spin.setValue(10)
        self.sit_posture_interval_spin.setSuffix(" 秒")
        self.sit_posture_interval_spin.setToolTip("坐下与起身指令之间、起身后稳定等待的安全间隔")
        lie_interval_label = QLabel("躺下起身间隔")
        lie_interval_label.setObjectName("controlHint")
        self.lie_posture_interval_spin = QSpinBox()
        self.lie_posture_interval_spin.setRange(5, 60)
        self.lie_posture_interval_spin.setValue(10)
        self.lie_posture_interval_spin.setSuffix(" 秒")
        self.lie_posture_interval_spin.setToolTip("躺下与起身指令之间、起身后稳定等待的安全间隔")
        interval_row.addWidget(sit_interval_label)
        interval_row.addWidget(self.sit_posture_interval_spin)
        interval_row.addSpacing(16)
        interval_row.addWidget(lie_interval_label)
        interval_row.addWidget(self.lie_posture_interval_spin)
        interval_row.addStretch(1)
        cycle_layout.addLayout(interval_row)

        cycle_buttons = QGridLayout()
        cycle_buttons.setHorizontalSpacing(16)
        cycle_buttons.setVerticalSpacing(12)
        self.sit_stand_cycle_btn = ControlButton("坐下起身 ×5")
        self.sit_stand_cycle_btn.setToolTip("站立开始：坐下 → 等待 → 起身 → 稳定等待，循环5次")
        self.sit_stand_cycle_btn.setMinimumHeight(44)
        self.sit_stand_cycle_btn.clicked.connect(lambda: self._start_posture_cycle("sit"))
        self.lie_stand_cycle_btn = ControlButton("躺下起身 ×5")
        self.lie_stand_cycle_btn.setToolTip("站立开始：躺下 → 等待 → 起身 → 稳定等待，循环5次")
        self.lie_stand_cycle_btn.setMinimumHeight(44)
        self.lie_stand_cycle_btn.clicked.connect(lambda: self._start_posture_cycle("lie"))
        self.stop_posture_cycle_btn = ControlButton("停止循环")
        self.stop_posture_cycle_btn.setToolTip("只停止后续循环调度，不会强行打断机器人正在执行的姿态动作")
        self.stop_posture_cycle_btn.setMinimumHeight(44)
        self.stop_posture_cycle_btn.clicked.connect(lambda: self._stop_posture_cycle("已停止姿态循环，当前动作如已下发请等待机器人自行完成"))
        self.stop_posture_cycle_btn.setEnabled(False)
        cycle_buttons.addWidget(self.sit_stand_cycle_btn, 0, 0)
        cycle_buttons.addWidget(self.lie_stand_cycle_btn, 0, 1)
        cycle_buttons.addWidget(self.stop_posture_cycle_btn, 1, 0, 1, 2)
        cycle_layout.addLayout(cycle_buttons)

        self.posture_cycle_status = QLabel("站稳后启动；吊装保护开启时禁止循环。")
        self.posture_cycle_status.setObjectName("controlHint")
        cycle_layout.addWidget(self.posture_cycle_status)
        layout.addWidget(cycle_grp)

        # Status hint
        self.mode_hint = QLabel("当前模式: 未知 | 行走模式: 需先进入阻尼模式")
        self.mode_hint.setObjectName("controlHint")
        layout.addWidget(self.mode_hint)

        # Result display
        self.result_display = QTextEdit()
        self.result_display.setObjectName("resultDisplay")
        self.result_display.setReadOnly(True)
        self.result_display.setMinimumHeight(96)
        self.result_display.setMaximumHeight(120)
        layout.addWidget(self.result_display)

        layout.addStretch(1)

    def _update_button_states(self):
        """Update buttons from robot mode and hanging safety guard."""
        walk_btn = self._tool_buttons.get("set_walk_mode")
        if walk_btn:
            can_walk = self._current_mode in WALK_ALLOWED_MODES
            walk_btn.setEnabled(can_walk)
            walk_btn.setToolTip("进入/保持行走模式" if can_walk else "需要先退出动作库/准备到可行走状态")

        protected = self.hanging_guard.isChecked() if hasattr(self, "hanging_guard") else True
        for tool in ("sit_down", "lie_down", "straight_walk"):
            btn = self._tool_buttons.get(tool)
            if btn:
                btn.setEnabled(not protected)
                btn.setToolTip("吊装保护已开启，禁止该动作" if protected else "确认机器人在地面且周围安全后执行")

        damping_unlocked = self.damping_guard.isChecked() if hasattr(self, "damping_guard") else False
        for tool in ("damping", "zero_torque"):
            btn = self._tool_buttons.get(tool)
            if btn:
                btn.setEnabled(damping_unlocked)
                btn.setToolTip("需先勾选“允许阻尼/零力矩按钮”；该 SDK 指令会让全身停止主动运动" if not damping_unlocked else "高风险：点击后将发送 SDK 瘫软类模式指令")

        for btn in (getattr(self, "sit_stand_cycle_btn", None), getattr(self, "lie_stand_cycle_btn", None)):
            if btn:
                btn.setEnabled((not protected) and (not self._posture_cycle_active))
                btn.setToolTip("吊装保护已开启，禁止姿态循环" if protected else "确认机器人站稳且周围安全后启动")
        if hasattr(self, "stop_posture_cycle_btn"):
            self.stop_posture_cycle_btn.setEnabled(self._posture_cycle_active)
        for spin in (getattr(self, "sit_posture_interval_spin", None), getattr(self, "lie_posture_interval_spin", None)):
            if spin:
                spin.setEnabled(not self._posture_cycle_active)

        standup_btn = self._tool_buttons.get("standup")
        if standup_btn:
            standup_btn.setText("悬吊站立" if protected else "站立")
            standup_btn.setToolTip("吊装保护下使用 hanging 起身模式" if protected else "从坐姿/躺姿起身，按当前状态选择 sitting/lying")

        # Update hint
        mode_names = {
            "damping": "阻尼模式", "zero_torque": "零力矩",
            "prepare": "准备站立", "standup": "站立中",
            "set_walk_mode": "拟人行走模式",
            "set_motion_engine_1": "动作库模式",
            "unknown": "未知",
        }
        mode_cn = mode_names.get(self._current_mode, self._current_mode)
        walk_ok = "可行走" if self._current_mode in WALK_ALLOWED_MODES else "需先退出动作库/准备到可行走状态"
        guard_text = "吊装保护: 开" if protected else "吊装保护: 关"
        self.mode_hint.setText(f"当前模式: {mode_cn} | 行走模式: {walk_ok} | {guard_text}")

        if self._allowed_tools is not None:
            for button_key, button in self._tool_buttons.items():
                tool_name = self.BUTTON_TOOLS.get(button_key)
                if tool_name and tool_name not in self._allowed_tools:
                    button.setEnabled(False)
                    button.setToolTip("当前机器人型号尚未开放此能力")
            cycle_allowed = all(
                tool in self._allowed_tools for tool in ("sit_down", "standup", "lie_down")
            )
            if not cycle_allowed:
                self.sit_stand_cycle_btn.setEnabled(False)
                self.lie_stand_cycle_btn.setEnabled(False)

    def apply_profile(self, profile: RobotProfile | None):
        self._allowed_tools = profile.allowed_tools if profile else frozenset()
        if "set_walk_velocity" not in self._allowed_tools:
            self._stop_straight_walk(update_ui=False)
        if self._posture_cycle_active:
            self._stop_posture_cycle("型号切换，已停止后续姿态循环")
        self._update_button_states()

    def update_robot_status(self, info: dict):
        status = info.get("robot_status", "")
        if not status or status == "?":
            return
        status_key = (status, info.get("ability", "?"), info.get("mode", "?"))
        if status_key == self._last_robot_status_key:
            return
        self._last_robot_status_key = status_key
        self._robot_status = status
        status_map = {
            "Damping": "damping",
            "ZeroTorque": "zero_torque",
            "Walk": "set_walk_mode",
            "Stand": "set_walk_mode",
            "Sit": "sit_down",
            "Prepare": "prepare",
            "Action": "set_motion_engine_1",
            "Menu": "set_motion_engine_1",
        }
        self._current_mode = status_map.get(status, self._current_mode)
        self._update_button_states()

    def _execute(self, tool: str, label: str):
        internal_tool = self.BUTTON_TOOLS.get(tool)
        if (
            self._allowed_tools is not None
            and internal_tool
            and internal_tool not in self._allowed_tools
        ):
            self.result_display.setText(f"{label}：当前机器人型号尚未开放此能力")
            return
        protected = self.hanging_guard.isChecked()
        if protected and tool in {"sit_down", "lie_down", "straight_walk"}:
            self.result_display.setText("吊装保护已开启：已拦截坐下/躺下/直线行走这类高风险动作")
            return
        if tool in {"damping", "zero_torque"}:
            if not self.damping_guard.isChecked():
                self.result_display.setText("已拦截：阻尼/零力矩按钮未解锁，未发送 SDK 指令")
                return
            text = "阻尼" if tool == "damping" else "零力矩"
            if not self._confirm_dangerous_mode(text):
                self.result_display.setText(f"已取消{text}指令，未发送 SDK request")
                return
        if tool != "straight_walk":
            self._stop_straight_walk(update_ui=False)
        if self._posture_cycle_active and tool not in {"sit_down", "lie_down", "standup"}:
            self._stop_posture_cycle("检测到手动控制，已停止后续姿态循环")

        standup_mode = "hanging" if protected else ("sitting" if self._robot_status == "Sit" else "lying")
        tool_map = {
            "prepare": ("prepare", {}),
            "standup": ("standup", {"mode": standup_mode, "enter_walk_after": not protected}),
            "sit_down": ("sit_down", {}),
            "lie_down": ("lie_down", {}),
            "safe_stop": ("safe_stop", {}),
            "set_walk_mode": ("set_walk_mode", {}),
            "set_motion_engine_1": ("set_motion_engine", {"mode": 1}),
            "damping": ("damping", {}),
            "zero_torque": ("zero_torque", {}),
            "get_action_library_status": ("get_action_library_status", {}),
            "audio_get_wakeup": ("audio_get_wakeup", {}),
            "audio_wakeup_enable": ("audio_wakeup_control", {"enable": True}),
            "audio_wakeup_disable": ("audio_wakeup_control", {"enable": False}),
            "straight_walk": ("straight_walk", {}),
        }
        if tool == "straight_walk":
            self._start_straight_walk()
            return
        if tool == "led_green":
            self._apply_led_effect(1, 3)
            return
        if tool == "led_breathe_blue":
            self._apply_led_effect(5, 5)
            return
        if tool == "led_off":
            self._apply_led_effect(0, 7)
            return
        info = tool_map.get(tool)
        if info:
            if tool == "standup":
                suffix = "，成功后进入拟人行走模式" if not protected else ""
                self.result_display.setText(f"站立指令: mode={standup_mode}{suffix}")
            elif tool == "safe_stop":
                self.result_display.setText("停止行走: 仅发送 x/y/yaw=0，不进入阻尼/零力矩")
            self.action_requested.emit(*info)

    def _confirm_dangerous_mode(self, mode_text: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(f"确认进入{mode_text}模式")
        box.setText(f"确认进入{mode_text}模式？")
        box.setInformativeText(
            f"SDK 文档说明：{mode_text}会让机器人所有电机停止主动运动。\n"
            "请确认机器人已扶稳或处于安全吊装状态，再继续发送 SDK 指令。"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        yes_button = box.button(QMessageBox.StandardButton.Yes)
        no_button = box.button(QMessageBox.StandardButton.No)
        if yes_button:
            yes_button.setText("确认进入")
            yes_button.setObjectName("dangerButton")
        if no_button:
            no_button.setText("取消")
            no_button.setObjectName("cancelButton")
        box.setStyleSheet(
            "QMessageBox { background: #FFFFFF; color: #1D2129; }"
            "QMessageBox QLabel { color: #1D2129; font-size: 14px; line-height: 1.5; min-width: 460px; }"
            "QMessageBox QPushButton { min-width: 96px; min-height: 36px; border-radius: 6px;"
            " padding: 6px 18px; font-size: 14px; font-weight: 600; }"
            "QMessageBox QPushButton#dangerButton { background: #B42318; color: #FFFFFF; border: 1px solid #B42318; }"
            "QMessageBox QPushButton#dangerButton:hover { background: #912018; border-color: #912018; }"
            "QMessageBox QPushButton#cancelButton { background: #FFFFFF; color: #1D2129; border: 1px solid #C9CDD4; }"
            "QMessageBox QPushButton#cancelButton:hover { background: #F2F3F5; border-color: #A9AEB8; }"
        )
        return box.exec() == QMessageBox.StandardButton.Yes

    def _start_posture_cycle(self, kind: str):
        if self.hanging_guard.isChecked():
            self.result_display.setText("吊装保护已开启：已拦截姿态循环测试")
            return
        self._stop_straight_walk(update_ui=False)
        self._posture_cycle_active = True
        self._posture_cycle_kind = kind
        self._posture_cycle_step = 0
        self._posture_cycle_total_steps = self._posture_cycle_count * 2
        label = "坐下起身" if kind == "sit" else "躺下起身"
        interval = self._posture_cycle_interval_seconds()
        self.result_display.setText(f"{label}循环启动：共 {self._posture_cycle_count} 次，安全间隔 {interval} 秒")
        self._update_button_states()
        self._run_next_posture_cycle_step()

    def _posture_cycle_interval_seconds(self) -> int:
        if self._posture_cycle_kind == "sit":
            return self.sit_posture_interval_spin.value()
        return self.lie_posture_interval_spin.value()

    def _run_next_posture_cycle_step(self):
        if not self._posture_cycle_active:
            return
        self._posture_cycle_waiting_for = ""
        if self._posture_cycle_step >= self._posture_cycle_total_steps:
            label = "坐下起身" if self._posture_cycle_kind == "sit" else "躺下起身"
            self._stop_posture_cycle(f"{label}循环完成：已执行 {self._posture_cycle_count} 次")
            return

        cycle_index = self._posture_cycle_step // 2 + 1
        is_down_step = self._posture_cycle_step % 2 == 0
        if self._posture_cycle_kind == "sit":
            down_tool = "sit_down"
            down_label = "坐下"
            standup_mode = "sitting"
        else:
            down_tool = "lie_down"
            down_label = "躺下"
            standup_mode = "lying"

        if is_down_step:
            self.posture_cycle_status.setText(f"第 {cycle_index}/{self._posture_cycle_count} 次：发送{down_label}，等待 SDK response")
            self.result_display.setText(self.posture_cycle_status.text())
            self._posture_cycle_waiting_for = down_tool
            self.action_requested.emit(down_tool, {})
        else:
            self.posture_cycle_status.setText(f"第 {cycle_index}/{self._posture_cycle_count} 次：发送起身，等待 SDK response")
            self.result_display.setText(self.posture_cycle_status.text())
            self._posture_cycle_waiting_for = "standup"
            self.action_requested.emit("standup", {"mode": standup_mode, "enter_walk_after": True})

    def _handle_posture_cycle_result(self, tool_name: str, result: dict, data: dict):
        if not result.get("success"):
            reason = data.get("result", "fail")
            self._stop_posture_cycle(f"姿态循环停止：{tool_name} 返回 {reason}")
            return

        if tool_name == "standup" and data.get("set_walk_mode") != "success":
            self._stop_posture_cycle("姿态循环停止：起身成功但进入行走模式失败，未继续下一步")
            return

        self._posture_cycle_step += 1
        interval_ms = self._posture_cycle_interval_seconds() * 1000
        self._posture_cycle_waiting_for = ""
        self.posture_cycle_status.setText(f"SDK 已返回成功，等待 {interval_ms // 1000} 秒后执行下一步")
        self.result_display.setText(self.posture_cycle_status.text())
        self._posture_cycle_timer = QTimer(self)
        self._posture_cycle_timer.setSingleShot(True)
        self._posture_cycle_timer.timeout.connect(self._run_next_posture_cycle_step)
        self._posture_cycle_timer.start(interval_ms)

    def _stop_posture_cycle(self, message: str):
        if self._posture_cycle_timer:
            self._posture_cycle_timer.stop()
            self._posture_cycle_timer.deleteLater()
            self._posture_cycle_timer = None
        self._posture_cycle_active = False
        self._posture_cycle_kind = ""
        self._posture_cycle_waiting_for = ""
        if hasattr(self, "posture_cycle_status"):
            self.posture_cycle_status.setText(message)
        if hasattr(self, "result_display"):
            self.result_display.setText(message)
        self._update_button_states()

    def _apply_led_effect(self, led_state: int, led_color: int):
        self._mcp.call_tool("enable_led_control", {"enable": True})
        self._mcp.call_tool("led_control", {
            "led_index": 0,
            "led_state": led_state,
            "led_color": led_color,
        })

    def _start_straight_walk(self):
        """持续发送行走指令10秒后自动停止。自动使用当前检测到的 accid。"""
        from config import detect_accid_from_wifi
        accid = detect_accid_from_wifi()
        if not accid:
            self.result_display.setText("错误: 未检测到机器人WiFi连接")
            return

        walk_btn = self._tool_buttons.get("straight_walk")
        if walk_btn:
            walk_btn.setEnabled(False)
            walk_btn.setText("行走中...")

        self.result_display.setText(f"直线行走测试启动\n机器: {accid}\n速度: x=0.7, 持续10秒")

        self._walk_step = 0
        self._walk_timer = QTimer(self)
        self._walk_timer.timeout.connect(lambda: self._send_walk_tick(accid))
        self._walk_timer.start(100)  # 10Hz

        # Auto-stop after 10 seconds
        QTimer.singleShot(10000, self._stop_straight_walk)

    def _send_walk_tick(self, accid: str):
        self._walk_step += 1
        self._mcp.call_tool("set_walk_velocity", {"x": 0.7, "y": 0.0, "yaw": 0.0})

    def _stop_straight_walk(self, update_ui: bool = True):
        was_walking = self._walk_timer is not None
        if was_walking:
            self._walk_timer.stop()
            self._walk_timer.deleteLater()
            self._walk_timer = None
            self._mcp.call_tool("set_walk_velocity", {"x": 0.0, "y": 0.0, "yaw": 0.0})
        if update_ui:
            self.result_display.setText("直线行走测试完成，机器人已停止" if was_walking else "当前没有正在运行的直线行走测试")

        walk_btn = self._tool_buttons.get("straight_walk")
        if walk_btn:
            walk_btn.setEnabled(True)
            walk_btn.setText("直线行走测试")
