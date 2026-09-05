from models.acceptance import (
    AcceptanceSessionPurpose,
    AcceptanceSessionStatus,
)
from models.robot_profile import L04_PROFILE, OLI_PROFILE
from ui.panels.acceptance_test_panel import AcceptanceTestPanel


class RecordingSessionRepository:
    def __init__(self):
        self.created = []
        self.results = []
        self.finished = []
        self.metadata_updates = []
        self.package_paths = []

    def create(self, session):
        self.created.append(session)

    def save_result(self, session_id, result):
        self.results.append((session_id, result))

    def finish(self, session):
        self.finished.append(session)

    def update_diagnostic_metadata(self, session):
        self.metadata_updates.append(session)

    def save_package_path(self, session_id, package_path):
        self.package_paths.append((session_id, package_path))


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


def test_one_click_diagnostic_requires_description_and_verified_target(
    qtbot,
    monkeypatch,
):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)

    panel.run_diagnostic()
    assert repository.created == []
    assert "填写故障描述" in panel.diagnostic_status.text()

    panel.diagnostic_description.setText("右臂无响应")
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_L04_01_091")
    panel.run_diagnostic()

    assert repository.created == []
    assert "身份未就绪" in panel.diagnostic_status.text()


def test_one_click_diagnostic_creates_bound_read_only_session(qtbot, monkeypatch):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    monkeypatch.setattr(config.ROBOT_CONFIG, "firmware_version", "robot-oli-r-2.4.10")
    started = []
    monkeypatch.setattr(panel, "_run_next_check", lambda: started.append(True))
    panel.diagnostic_description.setText("右臂上电后无响应")

    panel.run_diagnostic()

    session = repository.created[-1]
    assert session.purpose == AcceptanceSessionPurpose.DIAGNOSTIC
    assert session.robot_accid == "HU_D04_01_075"
    assert session.problem_description == "右臂上电后无响应"
    assert session.robot_firmware == "robot-oli-r-2.4.10"
    assert panel.CHECKS[0].key == "robot_info"
    assert panel.CHECKS[-1].key == "mros_services"
    assert started == [True]


def test_diagnostic_time_failure_never_runs_repair(qtbot, monkeypatch):
    import config
    from datetime import datetime
    import time
    from ui.panels.acceptance_test_panel import BEIJING_TIMEZONE

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel._start_acceptance_session(
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="时间异常",
    )
    row = next(
        index for index, check in enumerate(panel.CHECKS)
        if check.key == "main_time"
    )
    check = panel.CHECKS[row]
    finished = []
    fixes = []
    monkeypatch.setattr(panel, "_finish_check", lambda *args: finished.append(args))
    monkeypatch.setattr(panel, "_run_time_fix", lambda *args: fixes.append(args))

    panel._on_time_checked(
        row,
        check,
        "TIME=2000-01-01 00:00:00 +0800\nZONE=Asia/Shanghai",
        (datetime.now(BEIJING_TIMEZONE), time.monotonic()),
    )

    assert fixes == []
    assert "只读诊断模式" in finished[0][2]


def test_diagnostic_robot_identity_mismatch_cancels_session(qtbot, monkeypatch):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel.CHECKS = panel.CHECKS[:1]
    panel._populate_checks()
    panel._start_acceptance_session(
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="身份检查",
    )
    panel._pending = [0]

    panel._on_diagnostic_robot_info_done(0, {"sn": "HU_D04_01_999"})

    assert panel._active_session is None
    assert repository.finished[-1].status == AcceptanceSessionStatus.CANCELLED
    assert repository.results[-1][1].status.value == "FAIL"
    assert "身份不一致" in panel.diagnostic_status.text()
    assert panel.version_labels["software_version"].text() == "-"


def test_diagnostic_robot_info_records_versions_and_hardware_status(
    qtbot,
    monkeypatch,
):
    import config
    from ui.panels.acceptance_test_panel import build_diagnostic_checks

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel.CHECKS = build_diagnostic_checks(OLI_PROFILE)
    panel._populate_checks()
    panel._start_acceptance_session(
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="EtherCAT 偶发异常",
    )
    monkeypatch.setattr(panel, "_run_next_check", lambda: None)

    panel._on_diagnostic_robot_info_done(0, {
        "sn": "HU_D04_01_075",
        "software_version": "robot-oli-r-2.4.10",
        "pms_version": "2.2.8",
        "ecm_version": "1.1.18",
        "motor_version": "1: 1.2.37; 2: 1.2.37",
        "ethercat": "OK",
        "imu": "OK",
        "camera": "OK",
        "camera_service": "Enabled",
    })

    session = panel._active_session
    assert session.robot_firmware == "robot-oli-r-2.4.10"
    assert session.robot_versions["ecm_version"] == "1.1.18"
    assert repository.metadata_updates == [session]
    result = repository.results[-1][1]
    assert result.status.value == "PASS"
    assert "ethercat=OK" in result.summary
    assert '"ethercat": "OK"' in result.detail


def test_diagnostic_robot_info_marks_ethercat_failure(qtbot, monkeypatch):
    import config
    from ui.panels.acceptance_test_panel import build_diagnostic_checks

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel.CHECKS = build_diagnostic_checks(OLI_PROFILE)
    panel._populate_checks()
    panel._start_acceptance_session(
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="EtherCAT 异常",
    )
    monkeypatch.setattr(panel, "_run_next_check", lambda: None)

    panel._on_diagnostic_robot_info_done(0, {
        "sn": "HU_D04_01_075",
        "ethercat": "ERROR",
        "imu": "OK",
    })

    assert repository.results[-1][1].status.value == "FAIL"
    assert "ethercat=ERROR" in repository.results[-1][1].summary


def test_completed_diagnostic_exports_and_persists_path(qtbot, monkeypatch, tmp_path):
    import config
    import ui.panels.acceptance_test_panel as module

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(
        profile=OLI_PROFILE,
        session_repository=repository,
        diagnostic_output_root=tmp_path,
    )
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel._start_acceptance_session(
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="一键诊断",
    )
    exported = tmp_path / "diagnostic.zip"
    monkeypatch.setattr(
        module,
        "export_diagnostic_package",
        lambda session, output_root, secrets=(): exported,
    )

    panel._pending = []
    panel._run_next_check()

    assert repository.finished[-1].status == AcceptanceSessionStatus.COMPLETED
    assert repository.package_paths == [
        (repository.finished[-1].session_id, str(exported)),
    ]
    assert panel.export_diagnostic_btn.isEnabled()
    assert str(exported) in panel.diagnostic_status.text()


def test_same_profile_accid_change_discards_late_diagnostic_callback(
    qtbot,
    monkeypatch,
):
    import config

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel._start_acceptance_session(
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="旧机器人诊断",
    )
    context = panel._current_run_context()
    calls = []

    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_076")
    panel._run_if_current(
        panel._profile_generation,
        calls.append,
        "stale-result",
        run_context=context,
    )

    assert calls == []
    assert panel._active_session is None
    assert repository.finished[-1].status == AcceptanceSessionStatus.CANCELLED
    assert "目标已变化" in panel.diagnostic_status.text()


def test_run_all_restores_standard_checks_after_diagnostic(qtbot, monkeypatch):
    import config
    from ui.panels.acceptance_test_panel import build_diagnostic_checks

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(profile=OLI_PROFILE, session_repository=repository)
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel.CHECKS = build_diagnostic_checks(OLI_PROFILE)
    panel._populate_checks()
    monkeypatch.setattr(panel, "_run_next_check", lambda: None)

    panel.run_all_checks()

    keys = [check.key for check in panel.CHECKS]
    assert "robot_info" not in keys
    assert "mros_services" not in keys
    assert keys == list(OLI_PROFILE.acceptance_check_keys)
    assert repository.created[-1].purpose == AcceptanceSessionPurpose.ACCEPTANCE


def test_diagnostic_export_failure_can_be_retried(qtbot, monkeypatch, tmp_path):
    import config
    import ui.panels.acceptance_test_panel as module

    repository = RecordingSessionRepository()
    panel = AcceptanceTestPanel(
        profile=OLI_PROFILE,
        session_repository=repository,
        diagnostic_output_root=tmp_path,
    )
    qtbot.addWidget(panel)
    monkeypatch.setattr(config.ROBOT_CONFIG, "ws_accid", "HU_D04_01_075")
    panel._start_acceptance_session(
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="导出重试",
    )
    monkeypatch.setattr(
        module,
        "export_diagnostic_package",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    panel._pending = []
    panel._run_next_check()

    assert panel.export_diagnostic_btn.isEnabled()
    assert panel._last_diagnostic_session is repository.finished[-1]
    assert "disk full" in panel.diagnostic_status.text()
    assert repository.package_paths == []