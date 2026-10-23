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
    image_file_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages (chat_id, id);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    persona TEXT NOT NULL,
    context TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.executescript(SCHEMA)
        columns = [row[1] for row in await self._conn.execute_fetchall("PRAGMA table_info(messages)")]
        if "image_file_id" not in columns:
            await self._conn.execute("ALTER TABLE messages ADD COLUMN image_file_id TEXT")
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
        image_file_id: str | None = None,
    ) -> Message:
        created_at = created_at or datetime.now(timezone.utc)
        await self.conn.execute(
            "INSERT INTO messages"
            " (chat_id, tg_id, author, text, reply_to_author, image_file_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, tg_id, author, text, reply_to_author, image_file_id, created_at.isoformat()),
        )
        await self.conn.commit()
        return Message(author, text, created_at, tg_id, reply_to_author, image_file_id)

    async def recent(self, chat_id: int, limit: int) -> list[Message]:
        cursor = await self.conn.execute(
            "SELECT author, text, created_at, tg_id, reply_to_author, image_file_id FROM messages"
            " WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            Message(author, text, datetime.fromisoformat(created_at), tg_id, reply_to, image)
            for author, text, created_at, tg_id, reply_to, image in reversed(rows)
        ]

    async def get_setting(self, key: str) -> str | None:
        cursor = await self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def move_chat(self, old_chat_id: int, new_chat_id: int) -> None:
        await self.conn.execute(
            "UPDATE messages SET chat_id = ? WHERE chat_id = ?", (new_chat_id, old_chat_id)
        )
        await self.conn.commit()

    async def rules(self) -> list[str]:
        cursor = await self.conn.execute("SELECT text FROM rules ORDER BY id")
        return [row[0] for row in await cursor.fetchall()]

    async def add_rule(self, text: str) -> int:
        await self.conn.execute(
            "INSERT INTO rules (text, created_at) VALUES (?, ?)",
            (text, datetime.now(timezone.utc).isoformat()),
        )
        await self.conn.commit()
        return len(await self.rules())

    async def delete_rule(self, number: int) -> str | None:
        """Видаляє правило за номером у списку (з 1). Повертає його текст або None."""
        cursor = await self.conn.execute("SELECT id, text FROM rules ORDER BY id")
        rows = await cursor.fetchall()
        if not 1 <= number <= len(rows):
            return None
        rule_id, text = rows[number - 1]
        await self.conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        await self.conn.commit()
        return text

    async def clear_chat(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def bot_message(
        self, chat_id: int, tg_id: int, persona: str | None = None, text: str | None = None
    ) -> tuple[str, str, str] | None:
        """(персонаж, текст, останнє повідомлення користувача перед ним) за id повідомлення бота.

        У звичайних (не супер-) групах кожен бот бачить свої номери повідомлень, тому
        якщо за id не знайшли — шукаємо останнє повідомлення цього персонажа з таким текстом.
        """
        cursor = await self.conn.execute(
            "SELECT id, author, text FROM messages WHERE chat_id = ? AND tg_id = ? AND author != 'user'",
            (chat_id, tg_id),
        )
        row = await cursor.fetchone()
        if row is None and persona and text:
            cursor = await self.conn.execute(
                "SELECT id, author, text FROM messages WHERE chat_id = ? AND author = ? AND text = ?"
                " ORDER BY id DESC LIMIT 1",
                (chat_id, persona, text),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        row_id, author, text = row
        cursor = await self.conn.execute(
            "SELECT text FROM messages WHERE chat_id = ? AND id < ? AND author = 'user'"
            " ORDER BY id DESC LIMIT 1",
            (chat_id, row_id),
        )
        before = await cursor.fetchone()
        return author, text, before[0] if before else ""

    async def add_example(self, kind: str, persona: str, context: str, text: str) -> None:
        await self.conn.execute(
            "INSERT INTO examples (kind, persona, context, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, persona, context, text, datetime.now(timezone.utc).isoformat()),
        )
        await self.conn.commit()

    async def examples(self, kind: str, limit: int) -> list[tuple[str, str, str]]:
        """Останні приклади заданого типу: (персонаж, контекст, текст), від старіших до новіших."""
        cursor = await self.conn.execute(
            "SELECT persona, context, text FROM examples WHERE kind = ? ORDER BY id DESC LIMIT ?",
            (kind, limit),
        )
        return list(reversed(await cursor.fetchall()))
