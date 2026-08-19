from config import ROBOT_CONFIG
from ui.panels.acceptance_test_panel import AcceptanceTestPanel
from workers.ssh_worker import SshAuthenticationError, SshWorker


def _check_row(panel: AcceptanceTestPanel, key: str) -> int:
    return next(index for index, check in enumerate(panel.CHECKS) if check.key == key)


def test_fresh_clone_authentication_failure_requests_authorization(monkeypatch):
    class AuthenticationRequiredClient:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            raise SshAuthenticationError("authentication required")

        def close(self):
            pass

    monkeypatch.setattr("workers.ssh_worker.SshClient", AuthenticationRequiredClient)
    worker = SshWorker(
        "10.192.1.2",
        "limx",
        [],
        robot_id="HU_D04_01_201",
    )
    authorization_requests = []
    errors = []
    worker.authentication_required.connect(
        lambda host, username, robot_id: authorization_requests.append(
            (host, username, robot_id)
        )
    )
    worker.error_occurred.connect(errors.append)

    worker.run()

    assert authorization_requests == [
        ("10.192.1.2", "limx", "HU_D04_01_201")
    ]
    assert errors == []


def test_ssh_check_waits_for_authorization_then_retries(qtbot, monkeypatch):
    panel = AcceptanceTestPanel()
    qtbot.addWidget(panel)
    row_index = _check_row(panel, "main_ssh")
    check = panel.CHECKS[row_index]
    robot_id = "HU_D04_01_201"
    authorization_requests = []
    retries = []

    panel.ssh_authorization_required.connect(
        lambda host, username, requested_robot_id: authorization_requests.append(
            (host, username, requested_robot_id)
        )
    )
    monkeypatch.setattr(
        panel,
        "_run_ssh_check",
        lambda row, retried_check, retried_robot_id="": retries.append(
            (row, retried_check.key, retried_robot_id)
        ),
    )
    monkeypatch.setattr(ROBOT_CONFIG, "ws_accid", robot_id)

    panel._on_ssh_authentication_required(
        row_index,
        check,
        ROBOT_CONFIG.main_control_ip,
        ROBOT_CONFIG.main_control_user,
        robot_id,
    )

    assert panel.check_table.item(row_index, 3).text() == "等待授权"
    assert panel._ssh_retry == (row_index, check, robot_id)
    assert authorization_requests == [
        (ROBOT_CONFIG.main_control_ip, ROBOT_CONFIG.main_control_user, robot_id)
    ]

    panel.finish_ssh_authorization(True, "")

    assert panel._ssh_retry is None
    assert panel.check_table.item(row_index, 3).text() == "执行中"
    assert retries == [(row_index, "main_ssh", robot_id)]


def test_ssh_check_rejects_retry_after_robot_switch(qtbot, monkeypatch):
    panel = AcceptanceTestPanel()
    qtbot.addWidget(panel)
    row_index = _check_row(panel, "main_ssh")
    check = panel.CHECKS[row_index]
    original_robot_id = "HU_D04_01_201"
    retries = []

    monkeypatch.setattr(
        panel,
        "_run_ssh_check",
        lambda *args, **kwargs: retries.append((args, kwargs)),
    )
    panel._on_ssh_authentication_required(
        row_index,
        check,
        ROBOT_CONFIG.main_control_ip,
        ROBOT_CONFIG.main_control_user,
        original_robot_id,
    )
    monkeypatch.setattr(ROBOT_CONFIG, "ws_accid", "HU_D04_01_280")

    panel.finish_ssh_authorization(True, "")

    assert panel.check_table.item(row_index, 3).text() == "FAIL"
    assert "机器人已从" in panel.check_table.item(row_index, 4).text()
    assert retries == []