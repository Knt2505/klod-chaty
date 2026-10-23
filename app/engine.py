"""Ядро: приймає повідомлення, чекає паузу, питає режисера, генерує і відправляє.

Не залежить від Telegram: відправка йде через Sender, моделі — через Generator.
"""

import asyncio
import logging
import random
import time as clock_time
from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from . import director
from .db import Database
from .director import Action, Plan
from .models import DISPLAY_NAMES, Author, Message, Persona
from .personas import rules_block, system_prompt
from .style import humanize
from .transcript import build_prompt, clean_reply, render_history

log = logging.getLogger(__name__)

MAX_IMAGES = 2
IMAGE_CACHE_SIZE = 10
NIGHT_START = time(1, 0)  # «написати першим» після живої розмови ок і пізно ввечері
NIGHT_END = time(6, 30)
BETWEEN_SPEAKERS = (3.0, 6.0)  # секунд паузи, перш ніж другий персонаж почне писати


def typing_seconds(text: str) -> float:
    """Скільки людина набирала б це повідомлення (з межами, щоб не тягнути вічність)."""
    return min(1.5 + len(text) / 25, 7.0)


class Sender(Protocol):
    async def typing(self, chat_id: int, persona: Persona) -> None: ...

    async def send(
        self, chat_id: int, persona: Persona, text: str, reply_to: int | None = None
    ) -> int: ...

    async def react(self, chat_id: int, persona: Persona, message_id: int, emoji: str) -> None: ...

    async def download(self, file_id: str) -> bytes: ...


class Generator(Protocol):
    async def generate(
        self, persona: Persona, system: str, prompt: str, images: list[bytes]
    ) -> str: ...

    async def direct(self, system: str, prompt: str, schema: dict) -> dict: ...


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
        nudge_minutes: float = 5,
        rng: random.Random | None = None,
        typing_pace: float = 1.0,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db = db
        self.llm = llm
        self.sender = sender
        self.tz = ZoneInfo(timezone)
        self.debounce_seconds = debounce_seconds
        self.history_limit = history_limit
        self.nudge_minutes = nudge_minutes
        self.rng = rng or random.Random()
        self.typing_pace = typing_pace
        self.clock = clock or (lambda: datetime.now(tz=self.tz))
        self.user_name = "Друг"
        self._pending: dict[int, list[Message]] = {}
        self._timers: dict[int, asyncio.Task] = {}
        self._nudge_timers: dict[int, asyncio.Task] = {}
        self._nudged: set[int] = set()
        self._locks: dict[int, asyncio.Lock] = {}
        self._image_cache: dict[str, bytes] = {}

    # --- вхідні повідомлення -------------------------------------------------

    async def on_user_message(
        self,
        chat_id: int,
        text: str,
        tg_id: int | None,
        reply_to_author: Author | None,
        user_name: str | None,
        image_file_id: str | None = None,
    ) -> None:
        if user_name:
            self.user_name = user_name
        self._cancel(self._nudge_timers, chat_id)
        self._nudged.discard(chat_id)
        await self.set_quiet(chat_id, 0)  # написав сам — значить, уже не «не заважай»
        msg = await self.db.add_message(
            chat_id, "user", text, tg_id, reply_to_author, image_file_id=image_file_id
        )
        self._pending.setdefault(chat_id, []).append(msg)
        # Скасовуємо лише таймер, що ще чекає; відповідь, яка вже генерується, не чіпаємо.
        self._cancel(self._timers, chat_id)
        self._timers[chat_id] = asyncio.create_task(self._after_pause(chat_id))

    async def _after_pause(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
        except asyncio.CancelledError:
            return
        self._timers.pop(chat_id, None)
        async with self._lock(chat_id):
            await self.respond(chat_id)

    async def respond(self, chat_id: int) -> None:
        pending = self._pending.pop(chat_id, [])
        if not pending:
            return
        history = await self.db.recent(chat_id, self.history_limit)
        try:
            plan = await self._plan(history, director.REPLY_TASK.format(user_name=self.user_name))
        except Exception:
            log.exception("director failed, using simple rules")
            plan = director.fallback_plan(history, pending, self.rng)
        if plan.quiet_minutes:
            await self.set_quiet(chat_id, plan.quiet_minutes)
        await self._perform(chat_id, plan, target_message=pending[-1].tg_id)
        self._schedule_nudge(chat_id)

    # --- «написати першим», коли користувач мовчить ---------------------------

    def _schedule_nudge(self, chat_id: int) -> None:
        if self.nudge_minutes <= 0 or chat_id in self._nudged:
            return
        self._cancel(self._nudge_timers, chat_id)
        self._nudge_timers[chat_id] = asyncio.create_task(self._nudge_after(chat_id))

    async def _nudge_after(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(self.nudge_minutes * 60)
        except asyncio.CancelledError:
            return
        self._nudge_timers.pop(chat_id, None)
        async with self._lock(chat_id):
            await self.nudge(chat_id)

    async def nudge(self, chat_id: int) -> None:
        if chat_id in self._nudged or self._pending.get(chat_id):
            return
        if self.is_night() or await self.is_quiet(chat_id):
            log.info("chat %s: nudge skipped (night or quiet)", chat_id)
            return
        history = await self.db.recent(chat_id, self.history_limit)
        if not history or history[-1].author == "user":
            return
        self._nudged.add(chat_id)  # один раз на кожну паузу
        task = director.NUDGE_TASK.format(user_name=self.user_name, minutes=round(self.nudge_minutes))
        try:
            plan = await self._plan(history, task)
        except Exception:
            log.exception("director failed for nudge")
            return
        texts = [a for a in plan.actions if a.kind == "text"][:1]
        await self._perform(chat_id, Plan(plan.meaning, texts), target_message=None)

    # --- тиша і ніч ----------------------------------------------------------

    async def set_quiet(self, chat_id: int, minutes: int) -> None:
        until = self.clock() + timedelta(minutes=minutes) if minutes else None
        await self.db.set_setting(f"quiet_until:{chat_id}", until.isoformat() if until else "")
        if until:
            log.info("chat %s: quiet until %s", chat_id, until)

    async def is_quiet(self, chat_id: int) -> bool:
        value = await self.db.get_setting(f"quiet_until:{chat_id}")
        return bool(value) and self.clock() < datetime.fromisoformat(value)

    def is_night(self) -> bool:
        now = self.clock().astimezone(self.tz).time()
        return NIGHT_START <= now < NIGHT_END

    # --- виконання плану -----------------------------------------------------

    async def _plan(self, history: list[Message], task: str) -> Plan:
        rules = await self.db.rules()
        system = director.DIRECTOR_SYSTEM.format(
            user_name=self.user_name,
            default_quiet=director.DEFAULT_QUIET_MINUTES,
            rules=rules_block(rules, self.user_name),
        )
        now = self.clock().astimezone(self.tz)
        prompt = (
            f"Зараз {now:%A %H:%M}.\n\nІсторія чату:\n\n"
            f"{render_history(history, self.user_name, self.tz, with_ids=True)}\n\n{task}"
        )
        known_ids = {m.tg_id for m in history if m.tg_id}
        plan = director.parse_plan(await self.llm.direct(system, prompt, director.PLAN_SCHEMA))
        # Номер, якого нема в історії, — вигадка моделі: прибираємо reply.
        plan = Plan(
            plan.meaning,
            [a if a.reply_to in known_ids else replace(a, reply_to=0) for a in plan.actions],
            plan.quiet_minutes,
        )
        log.info("plan: %s | %s | quiet=%s", plan.meaning, plan.actions, plan.quiet_minutes)
        return plan

    async def _perform(self, chat_id: int, plan: Plan, target_message: int | None) -> None:
        queue = list(plan.actions)
        spoken: list[Persona] = []
        while queue:
            if self._interrupted(chat_id):
                log.info("chat %s: user wrote again, dropping %d planned actions", chat_id, len(queue))
                return
            action = queue.pop(0)
            if action.kind == "reaction":
                await self._react(chat_id, action, action.reply_to or target_message)
                continue
            intent = action.intent
            if spoken:
                # Другий у раунді: спершу «читає» відповідь першого, і пише коротко.
                await asyncio.sleep(self.rng.uniform(*BETWEEN_SPEAKERS) * self.typing_pace)
                if self._interrupted(chat_id):
                    return
                intent = (intent + ". " if intent else "") + "Коротко, одним повідомленням."
            text = await self._speak(chat_id, action.who, plan.meaning, intent, action.reply_to or None)
            spoken.append(action.who)
            # Звернувся до іншого персонажа на ім'я — той відповідає (один раз за раунд).
            other = director.other(action.who)
            already = other in spoken or any(a.who == other and a.kind == "text" for a in queue)
            if text and not already and other in director.mentioned(text):
                log.info("chat %s: %s called %s", chat_id, action.who, other)
                intent = f"{DISPLAY_NAMES[action.who]} звернувся до тебе — відповідай"
                queue.append(Action(other, "text", intent=intent))

    async def _react(self, chat_id: int, action: Action, message_id: int | None) -> None:
        if message_id is None:
            return
        try:
            await self.sender.react(chat_id, action.who, message_id, action.emoji)
        except Exception:
            log.exception("reaction %s by %s failed", action.emoji, action.who)
            return
        await self.db.add_message(chat_id, action.who, f"[реакція {action.emoji}]")

    async def _speak(
        self,
        chat_id: int,
        persona: Persona,
        meaning: str = "",
        intent: str = "",
        reply_to: int | None = None,
    ) -> str | None:
        """Генерує і відправляє репліку. Повертає відправлений текст (None — нічого не сказав)."""
        history = await self.db.recent(chat_id, self.history_limit)
        typing = asyncio.create_task(self._keep_typing(chat_id, persona))
        started = clock_time.monotonic()
        try:
            raw = await self._generate(persona, history, meaning, intent)
            if raw is None:
                return None
            sent = []
            for i, part in enumerate(humanize(clean_reply(raw))):
                # Час «набору» як у людини; для першої частини враховуємо, скільки модель уже думала.
                wait = typing_seconds(part) * self.typing_pace
                if i == 0:
                    wait -= clock_time.monotonic() - started
                if wait > 0:
                    await asyncio.sleep(wait)
                if self._interrupted(chat_id) and sent:
                    break
                # reply тільки на першій частині, як у людей
                tg_id = await self.sender.send(chat_id, persona, part, reply_to if i == 0 else None)
                await self.db.add_message(chat_id, persona, part, tg_id)
                sent.append(part)
            return " ".join(sent) or None
        finally:
            typing.cancel()

    async def _keep_typing(self, chat_id: int, persona: Persona) -> None:
        """«друкує…» в Telegram гасне через ~5 с, тому оновлюємо його, поки модель думає."""
        while True:
            try:
                await self.sender.typing(chat_id, persona)
            except Exception:
                log.warning("typing action failed", exc_info=True)
            await asyncio.sleep(4)

    async def _generate(
        self, persona: Persona, history: list[Message], meaning: str, intent: str
    ) -> str | None:
        attached, images = await self._recent_images(history)
        system = system_prompt(persona, self.user_name, await self.db.rules())

        def prompt(with_images: list[str]) -> str:
            return build_prompt(
                persona, history, self.user_name, self.tz, with_images, meaning=meaning, intent=intent
            )

        try:
            return await self.llm.generate(persona, system, prompt(attached), images)
        except Exception:
            log.exception("generation failed for %s (images: %d)", persona, len(images))
            if not images:
                return None
            try:
                return await self.llm.generate(persona, system, prompt([]), [])
            except Exception:
                log.exception("generation without images failed for %s", persona)
                return None

    async def _recent_images(self, history: list[Message]) -> tuple[list[str], list[bytes]]:
        """Останні фото з історії, які персонаж «бачить» (file_id по порядку і їхні байти)."""
        file_ids = [m.image_file_id for m in history if m.image_file_id][-MAX_IMAGES:]
        attached, images = [], []
        for file_id in file_ids:
            data = self._image_cache.get(file_id)
            if data is None:
                try:
                    data = await self.sender.download(file_id)
                except Exception:
                    log.exception("could not download image %s", file_id)
                    continue
                if len(self._image_cache) >= IMAGE_CACHE_SIZE:
                    self._image_cache.pop(next(iter(self._image_cache)))
                self._image_cache[file_id] = data
            attached.append(file_id)
            images.append(data)
        return attached, images

    # --- допоміжне -----------------------------------------------------------

    def _interrupted(self, chat_id: int) -> bool:
        """Користувач написав щось нове — поточний раунд згортаємо, відповімо на нове."""
        return bool(self._pending.get(chat_id))

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    @staticmethod
    def _cancel(timers: dict[int, asyncio.Task], chat_id: int) -> None:
        timer = timers.pop(chat_id, None)
        if timer:
            timer.cancel()
