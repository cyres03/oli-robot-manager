import sqlite3

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from config import APP_CONFIG, ROBOT_CONFIG
from database.connection import DatabaseConnection
from database.repository import DanceCountRepository
from services.dance_service import DanceService
from ui.panels.dance_library_panel import DanceLibraryPanel
from ui.widgets.dance_card import DanceCard
from workers.mcp_worker import McpWorker


def _worker():
    return McpWorker("ws://10.192.1.2:5000", "robot-1")


def test_reset_only_clears_matching_category(tmp_path, monkeypatch):
    monkeypatch.setattr(APP_CONFIG, "data_dir", str(tmp_path))
    DatabaseConnection().initialize_schema()
    repository = DanceCountRepository()

    repository.increment("robot-1", "wave", "dance")
    repository.increment("robot-1", "wave", "motion")
    repository.increment("robot-1", "wave", "motion")
    assert repository.get_count("robot-1", "wave", "dance") == 1
    assert repository.get_count("robot-1", "wave", "motion") == 2

    assert repository.reset("robot-1", "wave", "dance") is True
    assert repository.get_count("robot-1", "wave", "dance") == 0
    assert repository.get_count("robot-1", "wave", "motion") == 2

    connection = DatabaseConnection().get_connection()
    rows = connection.execute(
        "SELECT category, count, last_executed FROM dance_counts "
        "WHERE robot_accid = ? AND name = ? ORDER BY category",
        ("robot-1", "wave"),
    ).fetchall()
    connection.close()
    assert [(row["category"], row["count"]) for row in rows] == [
        ("dance", 0),
        ("motion", 2),
    ]
    assert rows[0]["last_executed"] is None
    assert rows[1]["last_executed"] is not None


def test_schema_migrates_robot_name_identity_to_include_category(tmp_path, monkeypatch):
    monkeypatch.setattr(APP_CONFIG, "data_dir", str(tmp_path))
    connection = DatabaseConnection().get_connection()
    connection.executescript("""
        CREATE TABLE dance_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot_accid TEXT NOT NULL,
            name TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            category TEXT NOT NULL DEFAULT 'dance',
            last_executed TIMESTAMP,
            UNIQUE(robot_accid, name)
        );
        INSERT INTO dance_counts (robot_accid, name, count, category)
        VALUES ('robot-1', 'wave', 2, 'motion');
    """)
    connection.commit()
    connection.close()

    DatabaseConnection().initialize_schema()
    repository = DanceCountRepository()
    repository.increment("robot-1", "wave", "dance")

    assert repository.get_count("robot-1", "wave", "motion") == 2
    assert repository.get_count("robot-1", "wave", "dance") == 1


def test_repository_reset_rolls_back_and_closes_on_sql_error(monkeypatch):
    class FailingConnection:
        rolled_back = False
        closed = False

        def execute(self, *_args):
            raise sqlite3.OperationalError("write failed")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    connection = FailingConnection()
    repository = DanceCountRepository()
    monkeypatch.setattr(repository._db, "get_connection", lambda: connection)

    with pytest.raises(sqlite3.OperationalError, match="write failed"):
        repository.reset("robot-1", "wave", "motion")

    assert connection.rolled_back is True
    assert connection.closed is True


def test_service_reset_updates_current_robot_cache(qtbot, monkeypatch):
    service = DanceService(_worker())
    monkeypatch.setattr(ROBOT_CONFIG, "ws_accid", "robot-1")
    reset_calls = []
    monkeypatch.setattr(
        service._count_repo,
        "reset",
        lambda robot_accid, name, category: (
            reset_calls.append((robot_accid, name, category)) or True
        ),
    )
    service._counts[("robot-1", "wave", "dance")] = 9
    service._counts[("robot-1", "wave", "motion")] = 4

    with qtbot.waitSignal(service.count_reset, timeout=1000) as signal:
        result = service.reset_count("wave", "motion")

    assert result is True
    assert reset_calls == [("robot-1", "wave", "motion")]
    assert service.get_count("wave", "dance") == 9
    assert service.get_count("wave", "motion") == 0
    assert signal.args == ["wave", "motion", 0]


def test_service_reset_keeps_cache_when_record_is_missing(monkeypatch):
    service = DanceService(_worker())
    monkeypatch.setattr(ROBOT_CONFIG, "ws_accid", "robot-1")
    monkeypatch.setattr(service._count_repo, "reset", lambda *_args: False)
    service._counts[("robot-1", "wave", "motion")] = 4
    reset_events = []
    errors = []
    service.count_reset.connect(lambda *args: reset_events.append(args))
    service.error_occurred.connect(errors.append)

    assert service.reset_count("wave", "motion") is False
    assert service.get_count("wave", "motion") == 4
    assert reset_events == []
    assert errors == ["未找到 wave 的执行次数记录"]


def test_service_reset_keeps_cache_on_database_error(monkeypatch):
    service = DanceService(_worker())
    monkeypatch.setattr(ROBOT_CONFIG, "ws_accid", "robot-1")

    def fail_reset(*_args):
        raise sqlite3.OperationalError("write failed")

    monkeypatch.setattr(service._count_repo, "reset", fail_reset)
    service._counts[("robot-1", "wave", "motion")] = 4
    reset_events = []
    errors = []
    service.count_reset.connect(lambda *args: reset_events.append(args))
    service.error_occurred.connect(errors.append)

    assert service.reset_count("wave", "motion") is False
    assert service.get_count("wave", "motion") == 4
    assert reset_events == []
    assert errors == ["清零 wave 执行次数失败: write failed"]


def test_dance_card_reset_button_is_labeled_and_emits(qtbot):
    card = DanceCard("挥手", "motion", count=3)
    qtbot.addWidget(card)

    assert card.reset_btn.text() == "清零"
    assert card.reset_btn.width() == 48
    with qtbot.waitSignal(card.reset_clicked, timeout=1000):
        qtbot.mouseClick(card.reset_btn, Qt.MouseButton.LeftButton)


def test_reset_confirmation_cancel_keeps_count(qtbot, monkeypatch):
    service = DanceService(_worker())
    monkeypatch.setattr(service, "get_count", lambda _name, _category: 3)
    reset_calls = []
    monkeypatch.setattr(
        service,
        "reset_count",
        lambda name, category: reset_calls.append((name, category)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    panel = DanceLibraryPanel(service)
    qtbot.addWidget(panel)
    panel._populate_motions([{"motion_name_en": "wave", "motion_name_cn": "挥手"}])

    panel._confirm_reset("wave", "motion")

    assert reset_calls == []
    assert panel._motion_cards["wave"].count_badge.text() == "3"
