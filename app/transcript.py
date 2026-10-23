import re
from zoneinfo import ZoneInfo

from .models import DISPLAY_NAMES, Message, Persona

NEW_ACQUAINTANCE_MESSAGES = 10


def author_name(author: str, user_name: str) -> str:
    return user_name if author == "user" else DISPLAY_NAMES[author]


def render_history(
    history: list[Message], user_name: str, tz: ZoneInfo, attached: list[str] = ()
) -> str:
    lines = []
    for msg in history:
        stamp = msg.created_at.astimezone(tz).strftime("%a %H:%M")
        name = author_name(msg.author, user_name)
        reply = (
            f" (у відповідь {author_name(msg.reply_to_author, user_name)})"
            if msg.reply_to_author
            else ""
        )
        image = ""
        if msg.image_file_id in attached:
            image = f" (фото №{attached.index(msg.image_file_id) + 1}, прикріплене — ти його бачиш)"
        elif msg.image_file_id:
            image = " (старе фото, ти його вже не бачиш)"
        lines.append(f"[{stamp}] {name}{reply}: {msg.text}{image}")
    return "\n".join(lines)


def build_prompt(
    persona: Persona,
    history: list[Message],
    user_name: str,
    tz: ZoneInfo,
    attached: list[str] = (),
) -> str:
    intro = (
        f"Ви щойно почали спілкуватися і майже нічого не знаєте про {user_name}.\n\n"
        if len(history) < NEW_ACQUAINTANCE_MESSAGES
        else ""
    )
    return (
        intro + "Останні повідомлення в груповому чаті:\n\n"
        f"{render_history(history, user_name, tz, attached)}\n\n"
        f"Напиши наступне повідомлення від імені {DISPLAY_NAMES[persona]}."
    )


_NAME_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)?(Клод|Чаті)\s*:\s*", re.IGNORECASE)


def clean_reply(text: str) -> str:
    """Прибирає «Клод:» чи «[Пн 18:02] Чаті:» на початку, якщо модель їх додала."""
    return _NAME_PREFIX.sub("", text, count=1).strip()
