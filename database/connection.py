import sqlite3
import threading
from config import APP_CONFIG


class DatabaseConnection:
    """Thread-safe singleton. Each thread opens its own connection."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(APP_CONFIG.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def initialize_schema(self):
        conn = self.get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS dance_counts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_accid TEXT NOT NULL DEFAULT '__legacy__',
                name TEXT UNIQUE NOT NULL,
                count INTEGER DEFAULT 0,
                category TEXT NOT NULL DEFAULT 'dance',
                last_executed TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dance_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS health_check_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                test_type TEXT NOT NULL DEFAULT 'manual',
                passed BOOLEAN DEFAULT 0,
                results_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS acceptance_sessions (
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
            );
            CREATE TABLE IF NOT EXISTS acceptance_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                check_key TEXT NOT NULL,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                UNIQUE(session_id, check_key),
                FOREIGN KEY(session_id) REFERENCES acceptance_sessions(session_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_acceptance_sessions_started_at
                ON acceptance_sessions(started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_acceptance_results_session
                ON acceptance_results(session_id);
        """)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(dance_counts)").fetchall()]
        indexes = conn.execute("PRAGMA index_list(dance_counts)").fetchall()
        has_name_only_unique = False
        for row in indexes:
            index_name = row[1]
            is_unique = bool(row[2])
            index_columns = [info[2] for info in conn.execute(f"PRAGMA index_info({index_name})").fetchall()]
            if is_unique and index_columns == ["name"]:
                has_name_only_unique = True
                break
        needs_migration = "robot_accid" not in columns or has_name_only_unique
        if needs_migration:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS dance_counts_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    robot_accid TEXT NOT NULL DEFAULT '__legacy__',
                    name TEXT NOT NULL,
                    count INTEGER DEFAULT 0,
                    category TEXT NOT NULL DEFAULT 'dance',
                    last_executed TIMESTAMP,
                    UNIQUE(robot_accid, name)
                );
            """)
            if "robot_accid" in columns:
                conn.execute(
                    """INSERT OR IGNORE INTO dance_counts_new
                       (id, robot_accid, name, count, category, last_executed)
                       SELECT id, robot_accid, name, count, category, last_executed FROM dance_counts"""
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO dance_counts_new
                       (id, robot_accid, name, count, category, last_executed)
                       SELECT id, '__legacy__', name, count, category, last_executed FROM dance_counts"""
                )
            conn.executescript("""
                DROP TABLE dance_counts;
                ALTER TABLE dance_counts_new RENAME TO dance_counts;
            """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_dance_counts_robot_name ON dance_counts(robot_accid, name)"
        )
        conn.commit()
        conn.close()
