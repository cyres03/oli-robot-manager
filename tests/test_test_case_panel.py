from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from models.managed_case import (
    HAND_FATIGUE_CAPABILITY,
    HAND_FATIGUE_RUNNER,
    TestCaseDefinition as CaseDefinition,
    TestRunResult as RunResult,
    TestRunStatus as RunStatus,
    TestRisk as Risk,
    TestSource as Source,
)
from ui.panels.managed_test_panel import TestCasePanel as ManagedTestCasePanel


class FakeService(QObject):
    cases_changed = pyqtSignal(list)
    run_started = pyqtSignal(object)
    output_line = pyqtSignal(str, str)
    run_finished = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, cases):
        super().__init__()
        self.cases = cases
        self.runs = []
        self.cancelled = False

    def available_cases(self):
        return self.cases

    def run_case(
        self,
        case_id,
        approved=False,
        local_script_path=None,
        arguments_override=None,
    ):
        self.runs.append(
            (case_id, approved, local_script_path, arguments_override)
        )
        case = next(case for case in self.cases if case.case_id == case_id)
        self.run_started.emit(case)

    def cancel_current(self):
        self.cancelled = True


def _case():
    return CaseDefinition(
        case_id="luna-main-snapshot",
        name="Luna 主控只读快照",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="节点健康",
        timeout_seconds=20,
        command="hostname",
    )


def _fatigue_case():
    return CaseDefinition(
        case_id="luna-hand-fatigue",
        name="双灵巧手疲劳测试",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.BUILTIN_RUNNER,
        category="灵巧手",
        timeout_seconds=7500,
        runner=HAND_FATIGUE_RUNNER,
        required_capability=HAND_FATIGUE_CAPABILITY,
        arguments=("7200", "10"),
        risks=frozenset({Risk.HARDWARE_CONTROL}),
    )


def test_panel_lists_and_runs_selected_case(qtbot, monkeypatch):
    service = FakeService([_case()])
    panel = ManagedTestCasePanel(service)
    qtbot.addWidget(panel)
    monkeypatch.setattr(panel, "_confirm_execution", lambda _case: True)

    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "Luna 主控只读快照"
    assert panel.table.item(0, 2).text() == ".2 主控"

    panel._run_selected()

    assert service.runs == [("luna-main-snapshot", True, None, None)]
    assert panel.cancel_btn.isEnabled()
    assert panel.table.item(0, 5).text() == "执行中"


def test_confirmation_dialog_uses_readable_chinese_buttons(qtbot, monkeypatch):
    panel = ManagedTestCasePanel(FakeService([_case()]))
    qtbot.addWidget(panel)
    captured = {}

    def capture_dialog(box):
        captured["text"] = box.text()
        captured["detail"] = box.informativeText()
        captured["confirm"] = box.button(QMessageBox.StandardButton.Yes).text()
        captured["cancel"] = box.button(QMessageBox.StandardButton.No).text()
        captured["style"] = box.styleSheet()
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", capture_dialog)

    assert panel._confirm_execution(_case()) is True
    assert captured["text"] == "Luna 主控只读快照"
    assert "远端命令" in captured["detail"]
    assert "remote_command" not in captured["detail"]
    assert captured["confirm"] == "确认执行"
    assert captured["cancel"] == "取消"
    assert "background: #FFFFFF" in captured["style"]


def test_hand_fatigue_confirmation_describes_motion_and_emergency_stop(
    qtbot,
    monkeypatch,
):
    case = _fatigue_case()
    panel = ManagedTestCasePanel(FakeService([case]))
    qtbot.addWidget(panel)
    captured = {}

    def capture_dialog(box):
        captured["detail"] = box.informativeText()
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", capture_dialog)

    assert panel._confirm_execution(case) is False
    assert "双手将在测试期间持续开合" in captured["detail"]
    assert "保持急停可用" in captured["detail"]
    assert "全程留人看护" in captured["detail"]


def test_panel_streams_output_and_renders_result(qtbot):
    case = _case()
    service = FakeService([case])
    panel = ManagedTestCasePanel(service)
    qtbot.addWidget(panel)
    panel._on_run_started(case)

    service.output_line.emit("node=luna", "stdout")
    service.output_line.emit("warning", "stderr")
    service.run_finished.emit(RunResult.create(
        session_id="session-1",
        case=case,
        accid="HU_L04_01_090",
        firmware="v1",
        target_host="10.192.1.2",
        status=RunStatus.PASS,
        started_at=datetime.now(),
        detail="测试通过",
    ))

    assert "node=luna" in panel.output.toPlainText()
    assert "[stderr] warning" in panel.output.toPlainText()
    assert "状态: PASS" in panel.output.toPlainText()
    assert "退出码: 无" in panel.output.toPlainText()
    assert "会话: session-1" in panel.output.toPlainText()
    assert "机器人: HU_L04_01_090" in panel.output.toPlainText()
    assert "目标: .2 主控 (10.192.1.2)" in panel.output.toPlainText()
    assert panel.table.item(0, 5).text() == "PASS"
    assert panel.run_btn.isEnabled()


def test_panel_uses_blue_white_surface_and_cleans_ansi_output(qtbot):
    case = _case()
    service = FakeService([case])
    panel = ManagedTestCasePanel(service)
    qtbot.addWidget(panel)
    panel._on_run_started(case)

    service.output_line.emit("\x1b[01;31mnode=luna\x1b[0m\x1b[K", "stdout")

    output = panel.output.toPlainText()
    assert "node=luna" in output
    assert "\x1b" not in output
    assert "01;31m" not in output
    assert panel.output.styleSheet() == ""
    assert "#F6F9FF" in panel.styleSheet()
    assert "#4F6BED" in panel.styleSheet()
    assert "#111827" not in panel.styleSheet()
    status_item = panel.table.item(0, 5)
    assert status_item.background().color().name().upper() == "#EAF0FF"


def test_panel_filters_by_node_category_and_risk(qtbot):
    main_case = _case()
    vision_case = CaseDefinition(
        case_id="luna-vision-risk",
        name="视觉持久化检查",
        product_key="hu_l04_01",
        target_role="speech_vision",
        source=Source.BUNDLED_SCRIPT,
        category="视觉",
        timeout_seconds=20,
        risks=frozenset({Risk.PERSISTENT_WRITE}),
    )
    service = FakeService([main_case, vision_case])
    panel = ManagedTestCasePanel(service)
    qtbot.addWidget(panel)

    panel.node_filter.setCurrentIndex(panel.node_filter.findData("speech_vision"))
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "视觉持久化检查"
    assert panel.table.item(0, 2).text() == ".4 语音/视觉"

    panel.node_filter.setCurrentIndex(0)
    panel.category_filter.setCurrentIndex(panel.category_filter.findData("节点健康"))
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "Luna 主控只读快照"

    panel.category_filter.setCurrentIndex(0)
    panel.risk_filter.setCurrentIndex(panel.risk_filter.findData("persistent_write"))
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "视觉持久化检查"

    panel.risk_filter.setCurrentIndex(panel.risk_filter.findData("__read_only__"))
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "Luna 主控只读快照"


def test_panel_configures_builtin_hand_fatigue_without_file_picker(qtbot, monkeypatch):
    case = _fatigue_case()
    service = FakeService([case])
    panel = ManagedTestCasePanel(service)
    qtbot.addWidget(panel)
    monkeypatch.setattr(panel, "_confirm_execution", lambda _case: True)

    assert panel.runner_options.isHidden() is False
    assert panel.duration_hours.value() == 2.0
    assert panel.duration_hours.maximum() == 2.07
    assert panel.cycles_per_phase.value() == 10
    panel.duration_hours.setValue(0.5)
    panel.cycles_per_phase.setValue(3)

    panel._run_selected()

    assert service.runs == [
        ("luna-hand-fatigue", True, None, ("1800", "3")),
    ]