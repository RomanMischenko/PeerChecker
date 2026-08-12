import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connection_scope(self) -> Generator[sqlite3.Connection, None, None]:
        import time
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                conn = sqlite3.connect(self.db_path, timeout=30.0)
                conn.row_factory = sqlite3.Row
                try:
                    with conn:
                        yield conn
                    break
                finally:
                    conn.close()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries:
                    time.sleep(0.2 * attempt)
                    continue
                raise

    def init_db(self) -> None:
        """Initialize database schema if tables do not exist."""
        logger.info(f"Initializing SQLite database schema at {self.db_path}")
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            # Table: peers
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS peers (
                    login TEXT PRIMARY KEY,
                    tribe_id INTEGER NOT NULL,
                    tribe_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_manual INTEGER DEFAULT 0,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    xp INTEGER DEFAULT 0,
                    logtime REAL DEFAULT 0.0,
                    suspicion_reason TEXT,
                    details_json TEXT
                )
                """
            )
            # Table: check_logs
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS check_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    new_peers_count INTEGER DEFAULT 0,
                    status_summary TEXT
                )
                """
            )
            # Table: bot_state
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def set_state(self, key: str, value: str) -> None:
        """Set or update key-value pair in bot_state table."""
        now = datetime.now().isoformat()
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, str(value), now),
            )
            conn.commit()

    def get_state(self, key: str, default: str | None = None) -> str | None:
        """Retrieve value by key from bot_state table."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def is_monitoring_active(self) -> bool:
        """Check if background monitoring state is enabled in persistent storage."""
        val = self.get_state("monitoring_active", "0")
        return val == "1"

    def set_monitoring_active(self, active: bool) -> None:
        """Save monitoring active status in persistent storage."""
        self.set_state("monitoring_active", "1" if active else "0")

    def is_check_in_progress(self) -> bool:
        """Check if a peer scan execution was in progress in persistent storage."""
        val = self.get_state("check_in_progress", "0")
        return val == "1"

    def set_check_in_progress(self, in_progress: bool) -> None:
        """Save check execution state in persistent storage."""
        self.set_state("check_in_progress", "1" if in_progress else "0")

    def get_known_logins(self) -> set[str]:
        """Retrieve set of all logins currently present in DB."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT login FROM peers")
            rows = cursor.fetchall()
            return {row["login"] for row in rows}

    def is_known_peer(self, login: str) -> bool:
        """Check if login already exists in DB."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM peers WHERE login = ?", (login,))
            return cursor.fetchone() is not None

    def save_peer(self, peer: dict[str, Any]) -> None:
        """Insert a single peer record."""
        self.save_peers_batch([peer])

    def save_peers_batch(self, peers: list[dict[str, Any]]) -> None:
        """Insert a batch of peer records."""
        if not peers:
            return

        now = datetime.now().isoformat()
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            for p in peers:
                cursor.execute(
                    """
                    INSERT INTO peers (
                        login, tribe_id, tribe_name, status, is_manual,
                        first_seen, updated_at, xp, logtime, suspicion_reason, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(login) DO UPDATE SET
                        tribe_id=excluded.tribe_id,
                        tribe_name=excluded.tribe_name,
                        status=CASE WHEN is_manual = 1 THEN status ELSE excluded.status END,
                        is_manual=CASE WHEN excluded.is_manual = 1 THEN 1 ELSE is_manual END,
                        updated_at=excluded.updated_at,
                        xp=excluded.xp,
                        logtime=excluded.logtime,
                        suspicion_reason=CASE WHEN is_manual = 1 THEN suspicion_reason ELSE excluded.suspicion_reason END,
                        details_json=excluded.details_json
                    """,
                    (
                        p["login"],
                        p["tribe_id"],
                        p["tribe_name"],
                        p["status"],
                        p.get("is_manual", 0),
                        p.get("first_seen", now),
                        now,
                        p.get("xp", 0),
                        p.get("logtime", 0.0),
                        p.get("suspicion_reason_text", p.get("suspicion_reason", "")),
                        json.dumps(p.get("details", {}), ensure_ascii=False),
                    ),
                )
            conn.commit()
            logger.info(f"Saved/Updated {len(peers)} peers in database.")

    def update_peer_status(self, login: str, new_status: str, is_manual: bool = True) -> bool:
        """
        Manually update status of a peer ('VERIFIED' or 'SUSPICIOUS').
        Sets is_manual=1 if updated by admin.
        """
        new_status = new_status.upper()
        if new_status not in ("VERIFIED", "SUSPICIOUS"):
            raise ValueError("Status must be 'VERIFIED' or 'SUSPICIOUS'")

        now = datetime.now().isoformat()
        reason_text = "Изменено вручную администратором" if is_manual else ""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE peers
                SET status = ?, is_manual = ?, updated_at = ?, suspicion_reason = ?
                WHERE login = ?
                """,
                (new_status, 1 if is_manual else 0, now, reason_text, login),
            )
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"Updated status for peer '{login}' to {new_status} (manual={is_manual})")
            return updated

    def get_peer(self, login: str) -> dict[str, Any] | None:
        """Retrieve a single peer record by login."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM peers WHERE login = ?", (login,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                if d.get("details_json"):
                    try:
                        d["details"] = json.loads(d["details_json"])
                    except Exception:
                        d["details"] = {}
                return d
            return None

    def get_peers_by_tribe(self, tribe_id: int) -> list[dict[str, Any]]:
        """Retrieve all peers for a given tribe."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM peers WHERE tribe_id = ? ORDER BY login ASC", (tribe_id,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_all_peers(self) -> list[dict[str, Any]]:
        """Retrieve all peers in the database."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM peers ORDER BY tribe_id ASC, login ASC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_filtered_peers(
        self, tribe_id: int | str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve peers filtered by optional tribe_id/tribe_name and/or status ('VERIFIED' or 'SUSPICIOUS')."""
        query = "SELECT * FROM peers WHERE 1=1"
        params: list[Any] = []

        if tribe_id is not None:
            if isinstance(tribe_id, int) or (isinstance(tribe_id, str) and tribe_id.isdigit()):
                query += " AND tribe_id = ?"
                params.append(int(tribe_id))
            elif isinstance(tribe_id, str) and tribe_id:
                query += " AND LOWER(tribe_name) LIKE ?"
                params.append(f"%{tribe_id.lower()}%")

        if status:
            query += " AND UPPER(status) = ?"
            params.append(status.upper())

        query += " ORDER BY tribe_id ASC, status ASC, login ASC"

        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, Any]:
        """Calculate aggregated peer stats grouped by tribe and status."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tribe_id, tribe_name, status, COUNT(*) as count
                FROM peers
                GROUP BY tribe_id, tribe_name, status
                """
            )
            rows = cursor.fetchall()

            stats: dict[str, Any] = {
                "by_tribe": {},
                "total": 0,
                "total_verified": 0,
                "total_suspicious": 0,
                "total_skipped_wave": 0,
            }
            for row in rows:
                tid = row["tribe_id"]
                tname = row["tribe_name"]
                status = row["status"]
                count = row["count"]

                if tid not in stats["by_tribe"]:
                    stats["by_tribe"][tid] = {
                        "tribe_name": tname,
                        "verified": 0,
                        "suspicious": 0,
                        "skipped_wave": 0,
                        "total": 0,
                    }

                if status == "VERIFIED":
                    stats["by_tribe"][tid]["verified"] += count
                    stats["total_verified"] += count
                elif status == "SKIPPED_WAVE":
                    stats["by_tribe"][tid]["skipped_wave"] += count
                    stats["total_skipped_wave"] += count
                else:
                    stats["by_tribe"][tid]["suspicious"] += count
                    stats["total_suspicious"] += count

                stats["by_tribe"][tid]["total"] += count
                stats["total"] += count

            return stats

    def log_check_run(self, new_peers_count: int, status_summary: str) -> int:
        """Record a monitoring check run entry."""
        now = datetime.now().isoformat()
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO check_logs (timestamp, new_peers_count, status_summary) VALUES (?, ?, ?)",
                (now, new_peers_count, status_summary),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_last_check_info(self) -> dict[str, Any] | None:
        """Retrieve the most recent check log entry."""
        with self.connection_scope() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM check_logs ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None
