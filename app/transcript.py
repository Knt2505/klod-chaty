import re
from zoneinfo import ZoneInfo

from .models import DISPLAY_NAMES, Message, Persona


def author_name(author: str, user_name: str) -> str:
    return user_name if author == "user" else DISPLAY_NAMES[author]


def render_history(history: list[Message], user_name: str, tz: ZoneInfo) -> str:
    lines = []
    for msg in history:
        stamp = msg.created_at.astimezone(tz).strftime("%a %H:%M")
        name = author_name(msg.author, user_name)
        reply = (
            f" (у відповідь {author_name(msg.reply_to_author, user_name)})"
            if msg.reply_to_author
            else ""
        )
        lines.append(f"[{stamp}] {name}{reply}: {msg.text}")
    return "\n".join(lines)


def build_prompt(persona: Persona, history: list[Message], user_name: str, tz: ZoneInfo) -> str:
    return (
        "Останні повідомлення в груповому чаті:\n\n"
        f"{render_history(history, user_name, tz)}\n\n"
        f"Напиши наступне повідомлення від імені {DISPLAY_NAMES[persona]}."
    )


_NAME_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)?(Клод|Чаті)\s*:\s*", re.IGNORECASE)


def clean_reply(text: str) -> str:
    """Прибирає «Клод:» чи «[Пн 18:02] Чаті:» на початку, якщо модель їх додала."""
    return _NAME_PREFIX.sub("", text, count=1).strip()
