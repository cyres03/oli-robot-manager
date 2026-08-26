import json
from datetime import datetime, timezone
from database.connection import DatabaseConnection
from models.acceptance import (
    AcceptanceItemResult,
    AcceptanceItemStatus,
    AcceptanceSession,
    AcceptanceSessionStatus,
)


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


class AcceptanceSessionRepository:
    def __init__(self, database: DatabaseConnection | None = None):
        self._db = database or DatabaseConnection()

    def create(self, session: AcceptanceSession):
        conn = self._db.get_connection()
        conn.execute(
            """INSERT INTO acceptance_sessions
               (session_id, robot_accid, profile_key, operator_name,
                software_version, started_at, completed_at, status,
                pass_count, fail_count, not_applicable_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.session_id,
                session.robot_accid,
                session.profile_key,
                session.operator_name,
                session.software_version,
                session.started_at,
                session.completed_at,
                session.status.value,
                session.pass_count,
                session.fail_count,
                session.not_applicable_count,
            ),
        )
        conn.commit()
        conn.close()

    def save_result(self, session_id: str, result: AcceptanceItemResult):
        conn = self._db.get_connection()
        conn.execute(
            """INSERT INTO acceptance_results
               (session_id, check_key, category, name, status, summary,
                detail, executed_at, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id, check_key) DO UPDATE SET
               category=excluded.category,
               name=excluded.name,
               status=excluded.status,
               summary=excluded.summary,
               detail=excluded.detail,
               executed_at=excluded.executed_at,
               note=excluded.note""",
            (
                session_id,
                result.check_key,
                result.category,
                result.name,
                result.status.value,
                result.summary,
                result.detail,
                result.executed_at,
                result.note,
            ),
        )
        conn.execute(
            """UPDATE acceptance_sessions SET
               pass_count=(SELECT COUNT(*) FROM acceptance_results
                           WHERE session_id=? AND status='PASS'),
               fail_count=(SELECT COUNT(*) FROM acceptance_results
                           WHERE session_id=? AND status='FAIL'),
               not_applicable_count=(SELECT COUNT(*) FROM acceptance_results
                                     WHERE session_id=? AND status='N/A')
               WHERE session_id=?""",
            (session_id, session_id, session_id, session_id),
        )
        conn.commit()
        conn.close()

    def recover_interrupted_sessions(self) -> int:
        conn = self._db.get_connection()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = conn.execute(
            """UPDATE acceptance_sessions
               SET status=?, completed_at=? WHERE status=?""",
            (
                AcceptanceSessionStatus.CANCELLED.value,
                now,
                AcceptanceSessionStatus.RUNNING.value,
            ),
        )
        conn.commit()
        recovered = cursor.rowcount
        conn.close()
        return recovered

    def finish(self, session: AcceptanceSession):
        conn = self._db.get_connection()
        conn.execute(
            """UPDATE acceptance_sessions SET
               completed_at=?, status=?, pass_count=?, fail_count=?,
               not_applicable_count=? WHERE session_id=?""",
            (
                session.completed_at,
                session.status.value,
                session.pass_count,
                session.fail_count,
                session.not_applicable_count,
                session.session_id,
            ),
        )
        conn.commit()
        conn.close()

    def get(self, session_id: str) -> AcceptanceSession | None:
        conn = self._db.get_connection()
        row = conn.execute(
            "SELECT * FROM acceptance_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            conn.close()
            return None
        item_rows = conn.execute(
            """SELECT check_key, category, name, status, summary, detail,
                      executed_at, note
               FROM acceptance_results WHERE session_id=? ORDER BY id""",
            (session_id,),
        ).fetchall()
        conn.close()
        return self._from_rows(row, item_rows)

    def list_recent(
        self,
        limit: int = 50,
        profile_key: str | None = None,
        robot_accid: str | None = None,
    ) -> list[AcceptanceSession]:
        clauses = []
        parameters: list[object] = []
        if profile_key:
            clauses.append("profile_key=?")
            parameters.append(profile_key)
        if robot_accid:
            clauses.append("robot_accid=?")
            parameters.append(robot_accid)
        where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        conn = self._db.get_connection()
        rows = conn.execute(
            f"SELECT * FROM acceptance_sessions{where_clause} "
            "ORDER BY started_at DESC LIMIT ?",
            parameters,
        ).fetchall()
        conn.close()
        return [self._from_rows(row, ()) for row in rows]

    @staticmethod
    def _from_rows(session_row, item_rows) -> AcceptanceSession:
        return AcceptanceSession(
            session_id=session_row["session_id"],
            robot_accid=session_row["robot_accid"],
            profile_key=session_row["profile_key"],
            operator_name=session_row["operator_name"],
            software_version=session_row["software_version"],
            started_at=session_row["started_at"],
            completed_at=session_row["completed_at"],
            status=AcceptanceSessionStatus(session_row["status"]),
            pass_count=session_row["pass_count"],
            fail_count=session_row["fail_count"],
            not_applicable_count=session_row["not_applicable_count"],
            items=[
                AcceptanceItemResult(
                    check_key=item["check_key"],
                    category=item["category"],
                    name=item["name"],
                    status=AcceptanceItemStatus(item["status"]),
                    summary=item["summary"],
                    detail=item["detail"],
                    executed_at=item["executed_at"],
                    note=item["note"],
                )
                for item in item_rows
            ],
        )


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
