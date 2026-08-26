from models.acceptance import AcceptanceSessionStatus
from models.robot_profile import L04_PROFILE, OLI_PROFILE
from ui.panels.acceptance_test_panel import AcceptanceTestPanel


class RecordingSessionRepository:
    def __init__(self):
        self.created = []
        self.results = []
        self.finished = []

    def create(self, session):
        self.created.append(session)

    def save_result(self, session_id, result):
        self.results.append((session_id, result))

    def finish(self, session):
        self.finished.append(session)

    def list_recent(self, **kwargs):
        return []

    def get(self, session_id):
        return None


def test_selected_check_creates_and_completes_session(qtbot, monkeypatch):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    panel.check_table.selectRow(0)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    monkeypatch.setattr(panel, "_run_local_check", lambda row, check: None)

    panel.run_selected_check()

    assert len(repository.created) == 1
    session = repository.created[0]
    assert session.robot_accid == "HU_D04_01_075"
    assert session.profile_key == "oli"
    assert session.software_version == "1.0.1"

    panel._finish_check(0, True, "当前 WiFi: HU_D04_01_075", "password=secret")

    assert len(repository.results) == 1
    assert repository.results[0][1].status.value == "PASS"
    assert "secret" not in repository.results[0][1].detail
    assert repository.finished[-1].status == AcceptanceSessionStatus.COMPLETED
    assert repository.finished[-1].pass_count == 1


def test_cancel_button_marks_running_session_cancelled(qtbot, monkeypatch):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    monkeypatch.setattr(panel, "_run_next_check", lambda: None)

    panel.run_all_checks()
    generation = panel._profile_generation
    panel.cancel_checks()

    assert panel._active_session is None
    assert panel._pending == []
    assert panel._profile_generation == generation + 1
    assert repository.finished[-1].status == AcceptanceSessionStatus.CANCELLED


def test_profile_switch_cancels_old_session(qtbot, monkeypatch):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    monkeypatch.setattr(panel, "_run_next_check", lambda: None)
    panel.run_all_checks()

    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_L04_01_091")
    panel.apply_profile(L04_PROFILE)

    assert panel._active_session is None
    assert repository.finished[-1].status == AcceptanceSessionStatus.CANCELLED


def test_failed_history_rerun_creates_new_session_without_overwriting_old(
    qtbot, monkeypatch
):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    monkeypatch.setattr(panel, "_run_next_check", lambda: None)

    panel.rerun_failed_checks(["portal", "missing-check"])

    assert len(repository.created) == 1
    assert panel._pending == [
        next(index for index, check in enumerate(panel.CHECKS) if check.key == "portal")
    ]
    assert panel.tabs.currentWidget() is panel.auto_tab