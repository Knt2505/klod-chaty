from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .models import Author, Message

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    tg_id INTEGER,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    reply_to_author TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages (chat_id, id);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def add_message(
        self,
        chat_id: int,
        author: Author,
        text: str,
        tg_id: int | None = None,
        reply_to_author: Author | None = None,
        created_at: datetime | None = None,
    ) -> Message:
        created_at = created_at or datetime.now(timezone.utc)
        await self.conn.execute(
            "INSERT INTO messages (chat_id, tg_id, author, text, reply_to_author, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, tg_id, author, text, reply_to_author, created_at.isoformat()),
        )
        await self.conn.commit()
        return Message(author, text, created_at, tg_id, reply_to_author)

    async def recent(self, chat_id: int, limit: int) -> list[Message]:
        cursor = await self.conn.execute(
            "SELECT author, text, created_at, tg_id, reply_to_author FROM messages"
            " WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            Message(author, text, datetime.fromisoformat(created_at), tg_id, reply_to)
            for author, text, created_at, tg_id, reply_to in reversed(rows)
        ]
