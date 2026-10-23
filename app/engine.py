"""Ядро: приймає повідомлення, чекає паузу, вирішує хто відповідає, генерує і відправляє.

Не залежить від Telegram: відправка йде через Sender, генерація через Generator.
"""

import asyncio
import logging
import random
from typing import Protocol
from zoneinfo import ZoneInfo

from . import director
from .db import Database
from .models import Author, Message, Persona
from .personas import system_prompt
from .style import humanize
from .transcript import build_prompt, clean_reply

log = logging.getLogger(__name__)


class Sender(Protocol):
    async def typing(self, chat_id: int, persona: Persona) -> None: ...

    async def send(self, chat_id: int, persona: Persona, text: str) -> int: ...


class Generator(Protocol):
    async def generate(self, persona: Persona, system: str, prompt: str) -> str: ...


class Engine:
    def __init__(
        self,
        db: Database,
        llm: Generator,
        sender: Sender,
        *,
        timezone: str,
        debounce_seconds: float,
        history_limit: int,
        rng: random.Random | None = None,
        typing_pace: float = 1.0,
    ):
        self.db = db
        self.llm = llm
        self.sender = sender
        self.tz = ZoneInfo(timezone)
        self.debounce_seconds = debounce_seconds
        self.history_limit = history_limit
        self.rng = rng or random.Random()
        self.typing_pace = typing_pace
        self.user_name = "Друг"
        self._pending: dict[int, list[Message]] = {}
        self._timers: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def on_user_message(
        self,
        chat_id: int,
        text: str,
        tg_id: int | None,
        reply_to_author: Author | None,
        user_name: str | None,
    ) -> None:
        if user_name:
            self.user_name = user_name
        msg = await self.db.add_message(chat_id, "user", text, tg_id, reply_to_author)
        self._pending.setdefault(chat_id, []).append(msg)
        self._restart_timer(chat_id)

    def _restart_timer(self, chat_id: int) -> None:
        # Скасовуємо лише таймер, що ще чекає; відповідь, яка вже генерується, не чіпаємо.
        timer = self._timers.pop(chat_id, None)
        if timer:
            timer.cancel()
        self._timers[chat_id] = asyncio.create_task(self._after_pause(chat_id))

    async def _after_pause(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
        except asyncio.CancelledError:
            return
        self._timers.pop(chat_id, None)
        async with self._locks.setdefault(chat_id, asyncio.Lock()):
            await self.respond(chat_id)

    async def respond(self, chat_id: int) -> None:
        pending = self._pending.pop(chat_id, [])
        if not pending:
            return
        history = await self.db.recent(chat_id, self.history_limit)
        speakers = director.decide(history, pending, self.rng)
        log.info("chat %s: speakers %s", chat_id, speakers)
        for persona in speakers:
            await self._speak(chat_id, persona)

    async def _speak(self, chat_id: int, persona: Persona) -> None:
        history = await self.db.recent(chat_id, self.history_limit)
        await self.sender.typing(chat_id, persona)
        try:
            raw = await self.llm.generate(
                persona,
                system_prompt(persona, self.user_name),
                build_prompt(persona, history, self.user_name, self.tz),
            )
        except Exception:
            log.exception("generation failed for %s", persona)
            return
        for i, part in enumerate(humanize(clean_reply(raw))):
            if i:
                await self.sender.typing(chat_id, persona)
                await asyncio.sleep(min(1 + len(part) / 40, 4) * self.typing_pace)
            tg_id = await self.sender.send(chat_id, persona, part)
            await self.db.add_message(chat_id, persona, part, tg_id)
