import json
from datetime import datetime, timezone
from database.connection import DatabaseConnection


class DanceCountRepository:
    def __init__(self):
        self._db = DatabaseConnection()

    def increment(self, robot_accid: str, name: str, category: str = "dance") -> int:
        conn = self._db.get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO dance_counts (robot_accid, name, count, category, last_executed)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(robot_accid, name) DO UPDATE SET
               count = count + 1, last_executed = excluded.last_executed""",
            (robot_accid, name, category, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT count FROM dance_counts WHERE robot_accid = ? AND name = ?", (robot_accid, name)
        ).fetchone()
        conn.close()
        return row["count"] if row else 0

    def get_count(self, robot_accid: str, name: str) -> int:
        conn = self._db.get_connection()
        row = conn.execute(
            "SELECT count FROM dance_counts WHERE robot_accid = ? AND name = ?", (robot_accid, name)
        ).fetchone()
        conn.close()
        return row["count"] if row else 0

    def get_all_counts(self) -> list[dict]:
        conn = self._db.get_connection()
        rows = conn.execute(
            "SELECT robot_accid, name, count, category, last_executed FROM dance_counts ORDER BY robot_accid, category, name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


class SequenceRepository:
    def __init__(self):
        self._db = DatabaseConnection()

    def save(self, name: str, steps_json: str) -> int:
        conn = self._db.get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO dance_sequences (name, steps_json, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (name, steps_json, now, now),
        )
        conn.commit()
        seq_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return seq_id

    def load_all(self) -> list[dict]:
        conn = self._db.get_connection()
        rows = conn.execute(
            "SELECT id, name, steps_json, created_at, updated_at FROM dance_sequences ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update(self, seq_id: int, name: str, steps_json: str):
        conn = self._db.get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE dance_sequences SET name=?, steps_json=?, updated_at=? WHERE id=?",
            (name, steps_json, now, seq_id),
        )
        conn.commit()
        conn.close()

    def delete(self, seq_id: int):
        conn = self._db.get_connection()
        conn.execute("DELETE FROM dance_sequences WHERE id=?", (seq_id,))
        conn.commit()
        conn.close()


class HealthCheckRepository:
    def __init__(self):
        self._db = DatabaseConnection()

    def save_result(self, test_type: str, passed: bool, results_json: str) -> int:
        conn = self._db.get_connection()
        conn.execute(
            """INSERT INTO health_check_results (test_type, passed, results_json)
               VALUES (?, ?, ?)""",
            (test_type, int(passed), results_json),
        )
        conn.commit()
        hc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return hc_id

    def get_history(self, limit: int = 50) -> list[dict]:
        conn = self._db.get_connection()
        rows = conn.execute(
            "SELECT id, run_at, test_type, passed, results_json FROM health_check_results ORDER BY run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


class SettingsRepository:
    def __init__(self):
        self._db = DatabaseConnection()

    def get(self, key: str, default: str = "") -> str:
        conn = self._db.get_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        return row["value"] if row else default

    def set(self, key: str, value: str):
        conn = self._db.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
        conn.close()

    def get_all(self) -> dict:
        conn = self._db.get_connection()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}
