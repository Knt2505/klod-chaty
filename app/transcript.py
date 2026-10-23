import re
from zoneinfo import ZoneInfo

from .models import DISPLAY_NAMES, Message, Persona

NEW_ACQUAINTANCE_MESSAGES = 10


def author_name(author: str, user_name: str) -> str:
    return user_name if author == "user" else DISPLAY_NAMES[author]


def render_history(
    history: list[Message],
    user_name: str,
    tz: ZoneInfo,
    attached: list[str] | None = None,
    with_ids: bool = False,
) -> str:
    """attached=None — не позначати фото взагалі (для режисера, який їх не бачить).
    with_ids — показати номери повідомлень, щоб режисер міг обрати, на що робити reply."""
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
        if attached is None or not msg.image_file_id:
            pass
        elif msg.image_file_id in attached:
            image = f" (фото №{attached.index(msg.image_file_id) + 1}, прикріплене — ти його бачиш)"
        else:
            image = " (старе фото, ти його вже не бачиш)"
        number = f"#{msg.tg_id} " if with_ids and msg.tg_id else ""
        lines.append(f"{number}[{stamp}] {name}{reply}: {msg.text}{image}")
    return "\n".join(lines)


def build_prompt(
    persona: Persona,
    history: list[Message],
    user_name: str,
    tz: ZoneInfo,
    attached: list[str] = (),
    *,
    meaning: str = "",
    intent: str = "",
) -> str:
    intro = (
        f"Ви щойно почали спілкуватися і майже нічого не знаєте про {user_name}.\n\n"
        if len(history) < NEW_ACQUAINTANCE_MESSAGES
        else ""
    )
    return (
        intro + "Останні повідомлення в груповому чаті:\n\n"
        f"{render_history(history, user_name, tz, list(attached))}\n\n"
        f"{_hint(meaning, intent)}"
        f"Напиши наступне повідомлення від імені {DISPLAY_NAMES[persona]}."
    )


def _hint(meaning: str, intent: str) -> str:
    lines = []
    if meaning:
        lines.append(f"Що відбувається: {meaning}")
    if intent:
        lines.append(f"Що тобі варто зробити: {intent}")
    if not lines:
        return ""
    return "Підказка (не цитуй її, просто врахуй):\n" + "\n".join(lines) + "\n\n"


_NAME_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)?(Клод|Чаті)\s*:\s*", re.IGNORECASE)


def clean_reply(text: str) -> str:
    """Прибирає «Клод:» чи «[Пн 18:02] Чаті:» на початку, якщо модель їх додала."""
    return _NAME_PREFIX.sub("", text, count=1).strip()
