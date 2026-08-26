from database.connection import DatabaseConnection
from database.repository import AcceptanceSessionRepository
from models.acceptance import (
    AcceptanceItemResult,
    AcceptanceItemStatus,
    AcceptanceSession,
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