from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from models.managed_case import (
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

    def run_case(self, case_id, approved=False, local_script_path=None):
        self.runs.append((case_id, approved, local_script_path))
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


def test_panel_lists_and_runs_selected_case(qtbot, monkeypatch):
    service = FakeService([_case()])
    panel = ManagedTestCasePanel(service)
    qtbot.addWidget(panel)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "Luna 主控只读快照"
    assert panel.table.item(0, 2).text() == ".2 主控"

    panel._run_selected()

    assert service.runs == [("luna-main-snapshot", True, None)]
    assert panel.cancel_btn.isEnabled()
    assert panel.table.item(0, 5).text() == "执行中"


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