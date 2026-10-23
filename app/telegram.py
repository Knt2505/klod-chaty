"""Telegram-шар: слухає групу через бота Клода, відправляє від імені обох ботів.

Боти не бачать повідомлень одне одного, тому слухає лише Клод (з вимкненим privacy mode),
а Чаті тільки відправляє. Свої репліки бекенд записує в історію сам.
"""

import logging
from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject
from aiogram.types import Message as TgMessage
from aiogram.types import ReactionTypeEmoji, ReplyParameters

from .config import Config
from .db import Database
from .engine import Engine
from .models import Persona

log = logging.getLogger(__name__)


class TelegramSender:
    def __init__(self, bots: dict[Persona, Bot]):
        self.bots = bots

    async def typing(self, chat_id: int, persona: Persona) -> None:
        await self.bots[persona].send_chat_action(chat_id, ChatAction.TYPING)

    async def send(
        self, chat_id: int, persona: Persona, text: str, reply_to: int | None = None
    ) -> int:
        reply = (
            ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
            if reply_to
            else None
        )
        sent = await self.bots[persona].send_message(chat_id, text, reply_parameters=reply)
        return sent.message_id

    async def react(self, chat_id: int, persona: Persona, message_id: int, emoji: str) -> None:
        await self.bots[persona].set_message_reaction(
            chat_id, message_id, reaction=[ReactionTypeEmoji(emoji=emoji)]
        )

    async def download(self, file_id: str) -> bytes:
        buffer = await self.bots["klod"].download(file_id)
        return buffer.read()


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
    if message.new_chat_title:
        return f"[назву групи змінено на «{message.new_chat_title}»]"
    if message.new_chat_photo:
        return "[змінено фото групи]"
    if message.delete_chat_photo:
        return "[фото групи видалено]"
    return None


def image_file_id(message: TgMessage) -> str | None:
    """Найбільша версія фото з повідомлення (Telegram завжди віддає JPEG)."""
    sizes = message.photo or message.new_chat_photo
    return sizes[-1].file_id if sizes else None


@dataclass
class Binding:
    """До якої групи і якої людини прив'язані боти (з .env або через /bind)."""

    chat_id: int | None
    owner_id: int | None

    @property
    def bound(self) -> bool:
        return self.chat_id is not None and self.owner_id is not None

    @classmethod
    async def load(cls, cfg: Config, db: Database) -> "Binding":
        chat_id = cfg.chat_id or _to_int(await db.get_setting("chat_id"))
        owner_id = cfg.owner_id or _to_int(await db.get_setting("owner_id"))
        return cls(chat_id, owner_id)

    async def save(self, db: Database, chat_id: int, owner_id: int) -> None:
        self.chat_id, self.owner_id = chat_id, owner_id
        await db.set_setting("chat_id", str(chat_id))
        await db.set_setting("owner_id", str(owner_id))


def _to_int(value: str | None) -> int | None:
    return int(value) if value else None


def build_router(
    engine: Engine, db: Database, binding: Binding, bot_ids: dict[int, Persona]
) -> Router:
    router = Router()

    def allowed(message: TgMessage) -> bool:
        user = message.from_user
        return (
            binding.bound
            and message.chat.id == binding.chat_id
            and user is not None
            and user.id == binding.owner_id
        )

    @router.message(Command("bind"))
    async def bind(message: TgMessage) -> None:
        if binding.bound or message.from_user is None:
            return
        if message.chat.type not in ("group", "supergroup"):
            await message.reply("Додай мене і Чаті в групу і напиши /bind там.")
            return
        await binding.save(db, message.chat.id, message.from_user.id)
        log.info("bound to chat %s, owner %s", binding.chat_id, binding.owner_id)
        await message.reply("готово, ми на місці. тепер це наш чат 🤝")

    @router.message(Command("rule", "правило"))
    async def add_rule(message: TgMessage, command: CommandObject) -> None:
        if not allowed(message):
            return
        text = (command.args or "").strip()
        if not text:
            await message.reply("напиши правило після команди, наприклад:\n/правило не допитуйся, якщо я пишу коротко")
            return
        number = await db.add_rule(text)
        await message.reply(f"записав, правило №{number} ✍️")

    @router.message(Command("rules", "правила"))
    async def list_rules(message: TgMessage) -> None:
        if not allowed(message):
            return
        rules = await db.rules()
        if not rules:
            await message.reply("правил поки нема. додати: /правило <текст>")
            return
        lines = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, 1))
        await message.reply(f"{lines}\n\nвидалити: /видали <номер>")

    @router.message(Command("unrule", "видали"))
    async def delete_rule(message: TgMessage, command: CommandObject) -> None:
        if not allowed(message):
            return
        args = (command.args or "").strip()
        removed = await db.delete_rule(int(args)) if args.isdigit() else None
        if removed is None:
            await message.reply("вкажи номер правила зі списку /правила, наприклад: /видали 2")
            return
        await message.reply(f"прибрав: {removed}")

    async def mark_example(message: TgMessage, kind: str) -> None:
        if not allowed(message):
            return
        original = message.reply_to_message
        found = None
        if original and original.from_user:
            found = await db.bot_message(
                message.chat.id,
                original.message_id,
                bot_ids.get(original.from_user.id),
                original.text,
            )
        persona_of_original = bot_ids.get(original.from_user.id) if original and original.from_user else None
        if found is None and persona_of_original and original.text:
            # У базі не знайшли (інша нумерація, стерта історія) — беремо текст прямо з reply.
            found = (persona_of_original, original.text, "")
        if found is None:
            await message.reply("зроби це reply на повідомлення Клода чи Чаті")
            return
        persona, text, context = found
        await db.add_example(kind, persona, context, text)
        try:
            await message.react([ReactionTypeEmoji(emoji="👌" if kind == "good" else "🤡")])
        except Exception:
            await message.reply("записав")

    @router.message(Command("top", "топ"))
    async def mark_good(message: TgMessage) -> None:
        await mark_example(message, "good")

    @router.message(Command("cringe", "кринж"))
    async def mark_bad(message: TgMessage) -> None:
        await mark_example(message, "bad")

    @router.message(Command("forget", "забудь"))
    async def forget(message: TgMessage) -> None:
        if not allowed(message):
            return
        await db.clear_chat(message.chat.id)
        await message.reply("все, чистий аркуш. попередню переписку ми не пам'ятаємо")

    @router.message(Command("ids"))
    async def show_ids(message: TgMessage) -> None:
        user_id = message.from_user.id if message.from_user else "?"
        await message.reply(f"CHAT_ID={message.chat.id}\nOWNER_ID={user_id}")

    @router.message()
    async def on_message(message: TgMessage) -> None:
        # Група стала супергрупою — Telegram змінив її ID, переносимо прив'язку й історію.
        if message.migrate_to_chat_id and message.chat.id == binding.chat_id:
            await db.move_chat(message.chat.id, message.migrate_to_chat_id)
            await binding.save(db, message.migrate_to_chat_id, binding.owner_id)
            log.info("chat migrated to %s", binding.chat_id)
            return
        if not allowed(message):
            if not binding.bound:
                log.warning("Боти ще не прив'язані — напиши /bind у своїй групі")
            return
        text = describe(message)
        if not text:
            return
        reply_to = None
        original = message.reply_to_message
        if original and original.from_user:
            reply_to = bot_ids.get(original.from_user.id)
            if reply_to is None and original.from_user.id == binding.owner_id:
                reply_to = "user"
        await engine.on_user_message(
            message.chat.id,
            text,
            message.message_id,
            reply_to,
            message.from_user.first_name if message.from_user else None,
            image_file_id(message),
        )

    return router
