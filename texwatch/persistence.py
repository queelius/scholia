"""SQLite-backed compilation history.

Stores one row per compilation with error/warning counts, duration,
word count, and page count. Messages (errors and warnings) are stored
in a child table linked by compile_id.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS compiles (
    id            INTEGER PRIMARY KEY,
    project       TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    success       BOOLEAN NOT NULL,
    duration_s    REAL,
    error_count   INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    word_count    INTEGER,
    page_count    INTEGER,
    main_file     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    compile_id  INTEGER NOT NULL REFERENCES compiles(id),
    level       TEXT NOT NULL,
    file        TEXT,
    line        INTEGER,
    message     TEXT NOT NULL
);
"""


class CompileStore:
    """SQLite store for compilation history."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def record_compile(
        self,
        project: str,
        success: bool,
        duration_s: float | None,
        error_count: int,
        warning_count: int,
        word_count: int | None,
        page_count: int | None,
        main_file: str,
        messages: list[dict[str, Any]],
    ) -> int:
        """Record a compilation result. Returns the compile id."""
        timestamp = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "INSERT INTO compiles (project, timestamp, success, duration_s, "
            "error_count, warning_count, word_count, page_count, main_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project, timestamp, success, duration_s, error_count,
             warning_count, word_count, page_count, main_file),
        )
        compile_id = cursor.lastrowid
        for msg in messages:
            self._conn.execute(
                "INSERT INTO messages (compile_id, level, file, line, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (compile_id, msg["level"], msg.get("file"),
                 msg.get("line"), msg["message"]),
            )
        self._conn.commit()
        return compile_id

    def query_compiles(
        self,
        project: str | None = None,
        since: str | None = None,
        limit: int = 50,
        success: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Query compilation history with optional filters."""
        clauses = []
        params: list[Any] = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if success is not None:
            clauses.append("success = ?")
            params.append(success)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = f"SELECT * FROM compiles{where} ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_compile_messages(self, compile_id: int) -> list[dict[str, Any]]:
        """Get errors/warnings for a specific compilation."""
        rows = self._conn.execute(
            "SELECT level, file, line, message FROM messages WHERE compile_id = ?",
            (compile_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
