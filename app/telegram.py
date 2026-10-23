"""Telegram-шар: слухає групу через бота Клода, відправляє від імені обох ботів.

Боти не бачать повідомлень одне одного, тому слухає лише Клод (з вимкненим privacy mode),
а Чаті тільки відправляє. Свої репліки бекенд записує в історію сам.
"""

import logging

from aiogram import Bot, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import Message as TgMessage

from .config import Config
from .engine import Engine
from .models import Persona

log = logging.getLogger(__name__)


class TelegramSender:
    def __init__(self, bots: dict[Persona, Bot]):
        self.bots = bots

    async def typing(self, chat_id: int, persona: Persona) -> None:
        await self.bots[persona].send_chat_action(chat_id, ChatAction.TYPING)

    async def send(self, chat_id: int, persona: Persona, text: str) -> int:
        sent = await self.bots[persona].send_message(chat_id, text)
        return sent.message_id


def describe(message: TgMessage) -> str | None:
    """Текстове представлення повідомлення для історії. None — ігнорувати."""
    caption = f" {message.caption}" if message.caption else ""
    if message.text:
        return message.text
    if message.photo:
        return f"[фото]{caption}"
    if message.sticker:
        return f"[стікер {message.sticker.emoji or ''}]".replace(" ]", "]")
    if message.voice:
        return "[голосове]"
    if message.video_note:
        return "[відеокружок]"
    if message.video:
        return f"[відео]{caption}"
    if message.animation:
        return f"[гіфка]{caption}"
    if message.document:
        return f"[файл]{caption}"
    return None


def build_router(engine: Engine, cfg: Config, bot_ids: dict[int, Persona]) -> Router:
    router = Router()

    def allowed(message: TgMessage) -> bool:
        if cfg.chat_id is None or cfg.owner_id is None:
            return False
        user = message.from_user
        return message.chat.id == cfg.chat_id and user is not None and user.id == cfg.owner_id

    @router.message(Command("ids"))
    async def show_ids(message: TgMessage) -> None:
        user_id = message.from_user.id if message.from_user else "?"
        await message.reply(f"CHAT_ID={message.chat.id}\nOWNER_ID={user_id}")

    @router.message()
    async def on_message(message: TgMessage) -> None:
        if not allowed(message):
            if cfg.chat_id is None or cfg.owner_id is None:
                log.warning("CHAT_ID/OWNER_ID не задано — напиши /ids у групі")
            return
        text = describe(message)
        if not text:
            return
        reply_to = None
        original = message.reply_to_message
        if original and original.from_user:
            reply_to = bot_ids.get(original.from_user.id)
            if reply_to is None and original.from_user.id == cfg.owner_id:
                reply_to = "user"
        await engine.on_user_message(
            message.chat.id,
            text,
            message.message_id,
            reply_to,
            message.from_user.first_name if message.from_user else None,
        )

    return router
