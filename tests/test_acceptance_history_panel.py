from database.connection import DatabaseConnection
from database.repository import AcceptanceSessionRepository
from models.acceptance import (
    AcceptanceItemResult,
    AcceptanceItemStatus,
    AcceptanceSession,
    AcceptanceSessionStatus,
)
from ui.panels.acceptance_history_panel import AcceptanceHistoryPanel


def _repository(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.APP_CONFIG, "data_dir", str(tmp_path))
    database = DatabaseConnection()
    database.initialize_schema()
    return AcceptanceSessionRepository(database)


def _session(repository, profile_key="oli", accid="HU_D04_01_075"):
    session = AcceptanceSession.create(
        robot_accid=accid,
        profile_key=profile_key,
        operator_name="tester",
        software_version="1.0.1",
    )
    repository.create(session)
    for key, status in (
        ("portal", AcceptanceItemStatus.FAIL),
        ("main_ssh", AcceptanceItemStatus.PASS),
    ):
        result = AcceptanceItemResult.create(
            check_key=key,
            category="测试",
            name=key,
            status=status,
            summary=key,
            detail=key,
            note="现场备注" if key == "portal" else "",
        )
        session.add_result(result)
        repository.save_result(session.session_id, result)
    session.finish(AcceptanceSessionStatus.COMPLETED)
    repository.finish(session)
    return session


def test_history_panel_filters_profile_and_loads_details(qtbot, tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    oli_session = _session(repository)
    _session(repository, "hu_l04_01", "HU_L04_01_091")
    panel = AcceptanceHistoryPanel(repository, "oli")
    qtbot.addWidget(panel)

    assert panel.session_table.rowCount() == 1
    panel.session_table.selectRow(0)

    assert oli_session.session_id in panel.detail_label.text()
    assert panel.result_table.rowCount() == 2
    assert panel.rerun_failed_button.isEnabled()
    notes = [
        panel.result_table.item(row, 5).text()
        for row in range(panel.result_table.rowCount())
    ]
    assert "现场备注" in notes


def test_history_panel_emits_only_failed_check_keys(qtbot, tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    _session(repository)
    panel = AcceptanceHistoryPanel(repository, "oli")
    qtbot.addWidget(panel)
    requested = []
    panel.rerun_requested.connect(requested.append)
    panel.session_table.selectRow(0)

    panel.rerun_failed_button.click()

    assert requested == [["portal"]]
