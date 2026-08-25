"""Per-user sqlite3 store: conversation history, document index, settings.

The database lives at `data/<userId>/store.db`. Every column that holds user
content - message text, rolling summaries, document summaries, original
filenames - is a BLOB sealed with the user's derived key. Only structural
columns (row ids, roles, timestamps, record UUIDs) are plaintext, because they
are what the queries order and join on.

`strings` over the database therefore shows sqlite's own schema keywords and
the literals 'user'/'assistant', and no message content.

Connections are opened per operation. These are small, infrequent queries for
a single-person assistant, and it keeps the store safe to call from the worker
threads that `asyncio.to_thread` hands out.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.crypto import Crypto

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL,
    content    BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS summaries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    upto_id    INTEGER NOT NULL,
    content    BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    record_id      TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    filename       BLOB NOT NULL,
    summary        BLOB NOT NULL,
    text_record_id TEXT,
    created_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


@dataclass(frozen=True)
class DocumentEntry:
    record_id: str
    kind: str
    filename: str
    summary: str
    text_record_id: str | None
    created_at: float


class UserStore:
    """All persisted state for one user."""

    def __init__(self, db_path: Path, user_id: str, crypto: Crypto) -> None:
        self._path = db_path
        self._user_id = user_id
        self._crypto = crypto
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        self._path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- conversation history -------------------------------------------------

    def add_turn(self, role: str, content: str) -> int:
        if role not in {"user", "assistant"}:
            raise ValueError(f"unexpected role: {role!r}")
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
                (
                    role,
                    self._crypto.seal_text(self._user_id, content, context="history"),
                    time.time(),
                ),
            )
            return int(cursor.lastrowid)

    def recent_turns(self, exchanges: int) -> list[Turn]:
        """Return the last `exchanges` user/assistant pairs, oldest first."""
        limit = max(0, exchanges) * 2
        if limit == 0:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Turn(role, self._crypto.open_text(self._user_id, blob, context="history"))
            for role, blob in reversed(rows)
        ]

    def message_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def turns_between(self, after_id: int, upto_id: int) -> list[Turn]:
        """Turns with `after_id` < id <= `upto_id`, oldest first.

        Compaction walks forward from the last summarised point, so the input
        to each summary stays bounded no matter how long the history gets.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE id > ? AND id <= ? ORDER BY id",
                (after_id, upto_id),
            ).fetchall()
        return [
            Turn(role, self._crypto.open_text(self._user_id, blob, context="history"))
            for role, blob in rows
        ]

    def latest_message_id(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(id) FROM messages").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # -- rolling summary ------------------------------------------------------

    def set_summary(self, upto_id: int, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO summaries (upto_id, content, created_at) VALUES (?, ?, ?)",
                (
                    upto_id,
                    self._crypto.seal_text(self._user_id, content, context="summary"),
                    time.time(),
                ),
            )

    def latest_summary(self) -> tuple[int, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT upto_id, content FROM summaries ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return int(row[0]), self._crypto.open_text(
            self._user_id, row[1], context="summary"
        )

    # -- document index -------------------------------------------------------

    def add_document(
        self,
        record_id: str,
        *,
        kind: str,
        filename: str,
        summary: str,
        text_record_id: str | None,
        created_at: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO documents "
                "(record_id, kind, filename, summary, text_record_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    kind,
                    self._crypto.seal_text(
                        self._user_id, filename, context=f"docname:{record_id}"
                    ),
                    self._crypto.seal_text(
                        self._user_id, summary, context=f"docsummary:{record_id}"
                    ),
                    text_record_id,
                    created_at,
                ),
            )

    def documents(self) -> list[DocumentEntry]:
        """Decrypt the whole summary index. This is the retrieval index."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_id, kind, filename, summary, text_record_id, created_at "
                "FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [
            DocumentEntry(
                record_id=row[0],
                kind=row[1],
                filename=self._crypto.open_text(
                    self._user_id, row[2], context=f"docname:{row[0]}"
                ),
                summary=self._crypto.open_text(
                    self._user_id, row[3], context=f"docsummary:{row[0]}"
                ),
                text_record_id=row[4],
                created_at=row[5],
            )
            for row in rows
        ]

    # -- settings -------------------------------------------------------------
    # Settings are configuration, not user content, so they stay readable. Keep
    # it that way: never put anything the user typed in here.

    def get_setting(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
