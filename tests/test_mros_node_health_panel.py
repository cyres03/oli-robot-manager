from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from models.managed_case import (
    TestCaseDefinition as CaseDefinition,
    TestRisk as Risk,
    TestRunResult as RunResult,
    TestRunStatus as RunStatus,
    TestSource as Source,
)
from ui.panels.mros_node_health_panel import MrosNodeHealthPanel


class FakeService(QObject):
    cases_changed = pyqtSignal(list)
    run_started = pyqtSignal(object)
    output_line = pyqtSignal(str, str)
    run_finished = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, case):
        super().__init__()
        self.case = case
        self.runs = []
        self.cancelled = False

    def available_cases(self):
        return [self.case]

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
        self.run_started.emit(self.case)

    def cancel_current(self):
        self.cancelled = True


def _case():
    return CaseDefinition(
        case_id="luna-mros-node-health",
        name="Luna mROS 节点健康",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.BUNDLED_SCRIPT,
        category="节点健康",
        timeout_seconds=15,
        script_path="scripts/mros_node_health.sh",
        interpreter="sh",
        arguments=(".",),
        risks=frozenset({Risk.TEMPORARY_WRITE}),
    )


def test_panel_runs_mros_case_with_grep_pattern(qtbot):
    service = FakeService(_case())
    panel = MrosNodeHealthPanel(service)
    qtbot.addWidget(panel)
    panel.pattern_input.setText("Gesture|Audio; touch /tmp/never")

    panel._run()

    assert service.runs == [(
        "luna-mros-node-health",
        False,
        None,
        ("Gesture|Audio; touch /tmp/never",),
    )]
    assert panel.cancel_btn.isEnabled()


def test_panel_classifies_streamed_mros_lines_and_result(qtbot):
    case = _case()
    service = FakeService(case)
    panel = MrosNodeHealthPanel(service)
    qtbot.addWidget(panel)
    panel._on_run_started(case)

    service.output_line.emit("GestureMrosNode online", "stdout")
    service.output_line.emit("AudioDeviceNode warning timeout", "stdout")
    service.output_line.emit("camera node disconnected", "stdout")
    service.run_finished.emit(RunResult.create(
        session_id="mros-session",
        case=case,
        accid="HU_L04_01_093",
        firmware="robot-luna-r-1.2.12",
        target_host="10.192.1.2",
        status=RunStatus.PASS,
        started_at=datetime.now(),
        detail="测试通过",
    ))

    assert panel.table.rowCount() == 3
    assert panel.table.item(0, 0).text() == "正常"
    assert panel.table.item(1, 0).text() == "警告"
    assert panel.table.item(2, 0).text() == "异常"
    assert panel.status_label.text().startswith("PASS: 3 行")


def test_panel_rejects_multiline_pattern(qtbot):
    service = FakeService(_case())
    panel = MrosNodeHealthPanel(service)
    qtbot.addWidget(panel)
    panel.pattern_input.setText("node\nerror")

    panel._run()

    assert service.runs == []
    assert "无效" in panel.status_label.text()