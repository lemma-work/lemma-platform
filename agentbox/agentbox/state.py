from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock

from agentbox.schemas import SandboxEnsureRequest
from agentbox.state_store.models import SandboxRecord, SessionRecord


class AgentBoxStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sandboxes (
                        sandbox_id TEXT PRIMARY KEY,
                        env_json TEXT NOT NULL,
                        idle_since_at REAL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                self._migrate_sandboxes_schema_locked()
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        sandbox_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        cwd TEXT NOT NULL,
                        env_keys_json TEXT NOT NULL,
                        last_active_at REAL NOT NULL,
                        active_operations INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (sandbox_id, session_id),
                        FOREIGN KEY (sandbox_id) REFERENCES sandboxes(sandbox_id)
                            ON DELETE CASCADE
                    )
                    """
                )
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def _migrate_sandboxes_schema_locked(self) -> None:
        columns = self._conn.execute("PRAGMA table_info(sandboxes)").fetchall()
        column_names = [str(column["name"]) for column in columns]
        expected_columns = [
            "sandbox_id",
            "env_json",
            "idle_since_at",
            "created_at",
            "updated_at",
        ]
        official_columns = {
            "desired_state",
            "desired_generation",
            "observed_generation",
            "provider_name",
            "provider_id",
            "instance_id",
            "last_active_at",
            "last_observed_at",
            "observed_state",
            "status_json",
            "endpoint_json",
            "last_failure",
            "reconcile_after",
        }
        extra_columns = set(column_names) - set(expected_columns)
        if (
            set(expected_columns).issubset(column_names)
            and extra_columns <= official_columns
        ):
            return

        now = time.time()
        expressions = {
            "sandbox_id": "sandbox_id" if "sandbox_id" in column_names else "''",
            "env_json": "COALESCE(env_json, '{}')"
            if "env_json" in column_names
            else "'{}'",
            "idle_since_at": "idle_since_at"
            if "idle_since_at" in column_names
            else "NULL",
            "created_at": (
                f"COALESCE(created_at, {now})"
                if "created_at" in column_names
                else str(now)
            ),
            "updated_at": (
                f"COALESCE(updated_at, {now})"
                if "updated_at" in column_names
                else str(now)
            ),
        }
        self._conn.execute("DROP TABLE IF EXISTS sandboxes_new")
        self._conn.execute(
            """
            CREATE TABLE sandboxes_new (
                sandbox_id TEXT PRIMARY KEY,
                env_json TEXT NOT NULL,
                idle_since_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            f"""
            INSERT OR REPLACE INTO sandboxes_new (
                sandbox_id, env_json, idle_since_at, created_at, updated_at
            )
            SELECT
                {expressions["sandbox_id"]},
                {expressions["env_json"]},
                {expressions["idle_since_at"]},
                {expressions["created_at"]},
                {expressions["updated_at"]}
            FROM sandboxes
            WHERE sandbox_id IS NOT NULL AND sandbox_id != ''
            """
        )
        self._conn.execute("DROP TABLE sandboxes")
        self._conn.execute("ALTER TABLE sandboxes_new RENAME TO sandboxes")

    def upsert_sandbox(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxRecord:
        now = time.time()
        env_json = json.dumps(request.env, sort_keys=True)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sandboxes (
                    sandbox_id, env_json, idle_since_at, created_at, updated_at
                )
                VALUES (?, ?, NULL, ?, ?)
                ON CONFLICT(sandbox_id) DO UPDATE SET
                    env_json = excluded.env_json,
                    idle_since_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    sandbox_id,
                    env_json,
                    now,
                    now,
                ),
            )
        return self.get_sandbox(sandbox_id) or self._record_from_request(
            sandbox_id, request
        )

    def ensure_sandbox_defaults(self, sandbox_id: str) -> SandboxRecord:
        existing = self.get_sandbox(sandbox_id)
        if existing is not None:
            return existing
        return self.upsert_sandbox(sandbox_id, SandboxEnsureRequest())

    def get_sandbox(self, sandbox_id: str) -> SandboxRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_id = ?",
                (sandbox_id,),
            ).fetchone()
        return self._record_from_row(row) if row else None

    def delete_sandbox(self, sandbox_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM sessions WHERE sandbox_id = ?", (sandbox_id,)
            )
            self._conn.execute(
                "DELETE FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            )

    def upsert_session(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        cwd: str,
        env_keys: list[str],
    ) -> SessionRecord:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    sandbox_id, session_id, cwd, env_keys_json, last_active_at,
                    active_operations, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(sandbox_id, session_id) DO UPDATE SET
                    cwd = excluded.cwd,
                    env_keys_json = excluded.env_keys_json,
                    last_active_at = excluded.last_active_at,
                    updated_at = excluded.updated_at
                """,
                (
                    sandbox_id,
                    session_id,
                    cwd,
                    json.dumps(sorted(env_keys)),
                    now,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE sandboxes SET idle_since_at = NULL, updated_at = ? WHERE sandbox_id = ?",
                (now, sandbox_id),
            )
        record = self.get_session(sandbox_id, session_id)
        if record is None:
            raise RuntimeError("failed to read newly upserted session")
        return record

    def touch_session(self, sandbox_id: str, session_id: str) -> bool:
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE sessions
                SET last_active_at = ?, updated_at = ?
                WHERE sandbox_id = ? AND session_id = ?
                """,
                (now, now, sandbox_id, session_id),
            )
            if cursor.rowcount:
                self._conn.execute(
                    "UPDATE sandboxes SET idle_since_at = NULL, updated_at = ? WHERE sandbox_id = ?",
                    (now, sandbox_id),
                )
            return bool(cursor.rowcount)

    def begin_operation(self, sandbox_id: str, session_id: str) -> bool:
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE sessions
                SET active_operations = active_operations + 1,
                    last_active_at = ?,
                    updated_at = ?
                WHERE sandbox_id = ? AND session_id = ?
                """,
                (now, now, sandbox_id, session_id),
            )
            if cursor.rowcount:
                self._conn.execute(
                    "UPDATE sandboxes SET idle_since_at = NULL, updated_at = ? WHERE sandbox_id = ?",
                    (now, sandbox_id),
                )
            return bool(cursor.rowcount)

    def end_operation(self, sandbox_id: str, session_id: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE sessions
                SET active_operations = MAX(active_operations - 1, 0),
                    last_active_at = ?,
                    updated_at = ?
                WHERE sandbox_id = ? AND session_id = ?
                """,
                (now, now, sandbox_id, session_id),
            )

    def get_session(self, sandbox_id: str, session_id: str) -> SessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM sessions
                WHERE sandbox_id = ? AND session_id = ?
                """,
                (sandbox_id, session_id),
            ).fetchone()
        return self._session_from_row(row) if row else None

    def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE sandbox_id = ? AND session_id = ?",
                (sandbox_id, session_id),
            )
            deleted = bool(cursor.rowcount)
            self.mark_idle_if_empty_locked(sandbox_id)
            return deleted

    def expired_sessions(self, idle_timeout_seconds: int) -> list[SessionRecord]:
        cutoff = time.time() - idle_timeout_seconds
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM sessions
                WHERE last_active_at < ? AND active_operations = 0
                ORDER BY last_active_at ASC
                """,
                (cutoff,),
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def idle_sandboxes(self, idle_timeout_seconds: int) -> list[SandboxRecord]:
        cutoff = time.time() - idle_timeout_seconds
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE sandboxes
                SET idle_since_at = ?
                WHERE idle_since_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM sessions
                    WHERE sessions.sandbox_id = sandboxes.sandbox_id
                  )
                """,
                (time.time(),),
            )
            rows = self._conn.execute(
                """
                SELECT * FROM sandboxes
                WHERE idle_since_at IS NOT NULL AND idle_since_at < ?
                ORDER BY idle_since_at ASC
                """,
                (cutoff,),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def mark_pod_stopped(self, sandbox_id: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sandboxes SET idle_since_at = ?, updated_at = ? WHERE sandbox_id = ?",
                (now, now, sandbox_id),
            )

    def mark_sandbox_active(self, sandbox_id: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sandboxes SET idle_since_at = NULL, updated_at = ? WHERE sandbox_id = ?",
                (now, sandbox_id),
            )

    def mark_idle_if_empty(self, sandbox_id: str) -> None:
        with self._lock, self._conn:
            self.mark_idle_if_empty_locked(sandbox_id)

    def mark_idle_if_empty_locked(self, sandbox_id: str) -> None:
        session_count = self._conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE sandbox_id = ?",
            (sandbox_id,),
        ).fetchone()[0]
        if session_count == 0:
            now = time.time()
            self._conn.execute(
                """
                UPDATE sandboxes
                SET idle_since_at = COALESCE(
                    idle_since_at, last_active_at, ?
                ), updated_at = ?
                WHERE sandbox_id = ?
                """,
                (now, now, sandbox_id),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _record_from_request(
        self, sandbox_id: str, request_obj: SandboxEnsureRequest
    ) -> SandboxRecord:
        return SandboxRecord(
            sandbox_id=sandbox_id,
            env=request_obj.env,
        )

    def _record_from_row(self, row: sqlite3.Row) -> SandboxRecord:
        columns = set(row.keys())
        status_data = None
        endpoint_data = None
        if "status_json" in columns and row["status_json"]:
            try:
                status_data = json.loads(row["status_json"])
            except (TypeError, json.JSONDecodeError):
                status_data = None
        if "endpoint_json" in columns and row["endpoint_json"]:
            try:
                endpoint_data = json.loads(row["endpoint_json"])
            except (TypeError, json.JSONDecodeError):
                endpoint_data = None
        return SandboxRecord(
            sandbox_id=row["sandbox_id"],
            env=json.loads(row["env_json"]),
            desired_state=(
                row["desired_state"] if "desired_state" in columns else "present"
            ),
            desired_generation=(
                int(row["desired_generation"]) if "desired_generation" in columns else 1
            ),
            observed_generation=(
                int(row["observed_generation"])
                if "observed_generation" in columns
                else 0
            ),
            provider_name=row["provider_name"] if "provider_name" in columns else None,
            provider_id=row["provider_id"] if "provider_id" in columns else None,
            instance_id=row["instance_id"] if "instance_id" in columns else None,
            observed_state=(
                row["observed_state"]
                if "observed_state" in columns and row["observed_state"]
                else "starting"
            ),
            status_data=status_data if isinstance(status_data, dict) else None,
            endpoint_data=endpoint_data if isinstance(endpoint_data, dict) else None,
            last_failure=(row["last_failure"] if "last_failure" in columns else None),
            reconcile_after=(
                float(row["reconcile_after"])
                if "reconcile_after" in columns and row["reconcile_after"] is not None
                else None
            ),
            idle_since_at=(
                float(row["idle_since_at"])
                if "idle_since_at" in columns and row["idle_since_at"] is not None
                else None
            ),
            last_active_at=(
                float(row["last_active_at"])
                if "last_active_at" in columns and row["last_active_at"] is not None
                else None
            ),
            last_observed_at=(
                float(row["last_observed_at"])
                if "last_observed_at" in columns and row["last_observed_at"] is not None
                else None
            ),
        )

    def _session_from_row(self, row: sqlite3.Row) -> SessionRecord:
        columns = set(row.keys())
        return SessionRecord(
            sandbox_id=row["sandbox_id"],
            session_id=row["session_id"],
            cwd=row["cwd"],
            env_keys=json.loads(row["env_keys_json"]),
            last_active_at=float(row["last_active_at"]),
            active_operations=int(row["active_operations"]),
            sandbox_generation=(
                int(row["sandbox_generation"]) if "sandbox_generation" in columns else 0
            ),
        )
