from database.connection import DatabaseConnection
from database.repository import AcceptanceSessionRepository
from models.acceptance import (
    AcceptanceItemResult,
    AcceptanceItemStatus,
    AcceptanceSession,
    AcceptanceSessionPurpose,
    AcceptanceSessionStatus,
    redact_acceptance_detail,
)


def _repository(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.APP_CONFIG, "data_dir", str(tmp_path))
    database = DatabaseConnection()
    database.initialize_schema()
    return AcceptanceSessionRepository(database)


def test_acceptance_session_round_trip_and_recent_filter(tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    session = AcceptanceSession.create(
        robot_accid="HU_D04_01_075",
        profile_key="oli",
        operator_name="tester",
        software_version="1.0.1",
    )
    repository.create(session)
    result = AcceptanceItemResult.create(
        check_key="main_ssh",
        category="SSH",
        name="主控 SSH 登录",
        status=AcceptanceItemStatus.PASS,
        summary="hostname",
        detail="robot-main",
    )
    session.add_result(result)
    repository.save_result(session.session_id, result)

    running = AcceptanceSessionRepository().get(session.session_id)
    assert running is not None
    assert running.pass_count == 1

    session.finish(AcceptanceSessionStatus.COMPLETED)
    repository.finish(session)

    loaded = AcceptanceSessionRepository().get(session.session_id)

    assert loaded is not None
    assert loaded.robot_accid == "HU_D04_01_075"
    assert loaded.status == AcceptanceSessionStatus.COMPLETED
    assert loaded.pass_count == 1
    assert loaded.fail_count == 0
    assert loaded.items == [result]
    assert repository.list_recent(profile_key="oli")[0].session_id == session.session_id
    assert repository.list_recent(profile_key="hu_l04_01") == []


def test_diagnostic_session_round_trip_redacts_description(tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    session = AcceptanceSession.create(
        robot_accid="HU_D04_01_075",
        profile_key="oli",
        operator_name="tester",
        software_version="1.0.1",
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="右臂异常 password=visible-secret",
        robot_firmware="robot-oli-r-24.4.10",
        robot_versions={"ecm": "1.2.3"},
        secrets=("visible-secret",),
    )

    repository.create(session)
    loaded = repository.get(session.session_id)

    assert loaded is not None
    assert loaded.purpose == AcceptanceSessionPurpose.DIAGNOSTIC
    assert loaded.problem_description == "右臂异常 password=[REDACTED]"
    assert loaded.robot_firmware == "robot-oli-r-24.4.10"
    assert loaded.robot_versions == {"ecm": "1.2.3"}

    repository.save_package_path(session.session_id, "/tmp/diagnostic.zip")
    assert repository.get(session.session_id).package_path == "/tmp/diagnostic.zip"


def test_acceptance_session_updates_same_check_and_counts(tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    session = AcceptanceSession.create(
        robot_accid="HU_D04_01_075",
        profile_key="oli",
        operator_name="tester",
        software_version="1.0.1",
    )
    repository.create(session)
    failed = AcceptanceItemResult.create(
        check_key="portal",
        category="网络",
        name="机器人信息页",
        status=AcceptanceItemStatus.FAIL,
        summary="HTTP 500",
        detail="failed",
    )
    passed = AcceptanceItemResult.create(
        check_key="portal",
        category="网络",
        name="机器人信息页",
        status=AcceptanceItemStatus.PASS,
        summary="HTTP 200",
        detail="ok",
    )

    session.add_result(failed)
    repository.save_result(session.session_id, failed)
    session.add_result(passed)
    repository.save_result(session.session_id, passed)
    session.finish(AcceptanceSessionStatus.COMPLETED)
    repository.finish(session)
    loaded = repository.get(session.session_id)

    assert loaded is not None
    assert loaded.pass_count == 1
    assert loaded.fail_count == 0
    assert len(loaded.items) == 1
    assert loaded.items[0].summary == "HTTP 200"


def test_acceptance_detail_redacts_runtime_secrets_and_private_keys():
    detail = (
        'password="visible-secret" sudo_password=second-secret\n'
        "-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-data\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )

    sanitized = redact_acceptance_detail(
        detail,
        secrets=("visible-secret", "second-secret"),
    )

    assert "visible-secret" not in sanitized
    assert "second-secret" not in sanitized
    assert "private-data" not in sanitized
    assert sanitized.count("[REDACTED]") == 2
    assert "[REDACTED PRIVATE KEY]" in sanitized

    result = AcceptanceItemResult.create(
        check_key="secret",
        category="测试",
        name="脱敏",
        status=AcceptanceItemStatus.FAIL,
        summary="password=visible-secret",
        detail=detail,
        note="second-secret",
        secrets=("visible-secret", "second-secret"),
    )
    assert "visible-secret" not in result.summary
    assert "second-secret" not in result.note


def test_acceptance_detail_redacts_tokens_bearer_headers_and_urls():
    detail = (
        'api_key="api-secret" access-token=access-secret\n'
        'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature\n'
        'https://example.test/path?token=query-secret&safe=value\n'
        '{"client_secret": "json-secret", "safe": "visible"}'
    )

    sanitized = redact_acceptance_detail(detail)

    for secret in (
        "api-secret",
        "access-secret",
        "eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "query-secret",
        "json-secret",
    ):
        assert secret not in sanitized
    assert "safe=value" in sanitized
    assert '"safe": "visible"' in sanitized


def test_cancelled_session_persists_completion_time(tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    session = AcceptanceSession.create(
        robot_accid="HU_L04_01_091",
        profile_key="hu_l04_01",
        operator_name="tester",
        software_version="1.0.1",
    )
    repository.create(session)
    session.finish(AcceptanceSessionStatus.CANCELLED)
    repository.finish(session)

    loaded = repository.get(session.session_id)

    assert loaded is not None
    assert loaded.status == AcceptanceSessionStatus.CANCELLED
    assert loaded.completed_at is not None


def test_repository_recovers_interrupted_sessions(tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    session = AcceptanceSession.create(
        robot_accid="HU_D04_01_075",
        profile_key="oli",
        operator_name="tester",
        software_version="1.0.1",
    )
    repository.create(session)

    assert AcceptanceSessionRepository().recover_interrupted_sessions() == 1

    loaded = repository.get(session.session_id)
    assert loaded is not None
    assert loaded.status == AcceptanceSessionStatus.CANCELLED
    assert loaded.completed_at is not None


def test_initialize_schema_migrates_legacy_acceptance_sessions(tmp_path, monkeypatch):
    import config
    import sqlite3

    monkeypatch.setattr(config.APP_CONFIG, "data_dir", str(tmp_path))
    connection = sqlite3.connect(config.APP_CONFIG.db_path)
    connection.execute(
        """CREATE TABLE acceptance_sessions (
           session_id TEXT PRIMARY KEY,
           robot_accid TEXT NOT NULL,
           profile_key TEXT NOT NULL,
           operator_name TEXT NOT NULL,
           software_version TEXT NOT NULL,
           started_at TEXT NOT NULL,
           completed_at TEXT,
           status TEXT NOT NULL,
           pass_count INTEGER NOT NULL DEFAULT 0,
           fail_count INTEGER NOT NULL DEFAULT 0,
           not_applicable_count INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.commit()
    connection.close()

    DatabaseConnection().initialize_schema()

    connection = sqlite3.connect(config.APP_CONFIG.db_path)
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(acceptance_sessions)"
        ).fetchall()
    }
    connection.close()
    assert {
        "purpose",
        "problem_description",
        "robot_firmware",
        "robot_versions_json",
        "package_path",
    } <= columns


def test_repository_tolerates_corrupt_robot_versions_json(tmp_path, monkeypatch):
    repository = _repository(tmp_path, monkeypatch)
    session = AcceptanceSession.create(
        robot_accid="HU_D04_01_075",
        profile_key="oli",
        operator_name="tester",
        software_version="1.0.1",
    )
    repository.create(session)
    connection = repository._db.get_connection()
    connection.execute(
        "UPDATE acceptance_sessions SET robot_versions_json=? WHERE session_id=?",
        ("not-json", session.session_id),
    )
    connection.commit()
    connection.close()

    assert repository.get(session.session_id).robot_versions == {}