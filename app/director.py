"""Режисер: хто, як і чи взагалі реагує.

Основний — LLM-режисер (plan). Прості правила (decide) — запасний варіант.
"""

import random
import re
from dataclasses import dataclass

from .models import Message, Persona

# «у чаті» / «в чаті» / «на чаті» — це про сам чат, а не звернення до Чаті.
_NAME_PATTERNS: dict[Persona, re.Pattern[str]] = {
    "klod": re.compile(r"\b(клод\w*|claude)\b", re.IGNORECASE),
    "chaty": re.compile(r"(?<!\bу )(?<!\bв )(?<!\bна )\b(чаті|chaty|gpt|гпт|джипіті)\b", re.IGNORECASE),
}

SWITCH_CHANCE = 0.25


def mentioned(text: str) -> list[Persona]:
    """Персонажі, до яких звернулись на ім'я, у порядку появи в тексті."""
    found = []
    for persona, pattern in _NAME_PATTERNS.items():
        match = pattern.search(text)
        if match:
            found.append((match.start(), persona))
    return [persona for _, persona in sorted(found)]


def other(persona: Persona) -> Persona:
    return "chaty" if persona == "klod" else "klod"


def decide(
    history: list[Message], pending: list[Message], rng: random.Random | None = None
) -> list[Persona]:
    """Хто відповідає на нові повідомлення користувача (pending), у порядку відповіді."""
    rng = rng or random.Random()
    if not pending:
        return []

    for msg in reversed(pending):
        if msg.reply_to_author in ("klod", "chaty"):
            return [msg.reply_to_author]

    names = mentioned(" ".join(msg.text for msg in pending))
    if names:
        return names

    last_bot = next((m.author for m in reversed(history) if m.author != "user"), None)
    if last_bot is None:
        return [rng.choice(("klod", "chaty"))]
    if rng.random() < SWITCH_CHANCE:
        return [other(last_bot)]
    return [last_bot]


# ---------------------------------------------------------------------------
# LLM-режисер: розуміє сенс і планує реакцію (текст / емодзі / тиша).
# ---------------------------------------------------------------------------

# Лише стандартні реакції Telegram, які доступні в групах без Premium.
REACTIONS = ("👍", "❤", "🔥", "🤣", "😁", "🤔", "👀", "💯", "🫡", "🤝", "😢", "🙏", "👌", "😎", "🗿", "🤯", "🎉", "🥱", "😴", "🤡", "❤‍🔥")
MAX_ACTIONS = 2
DEFAULT_QUIET_MINUTES = 180


@dataclass(frozen=True)
class Action:
    who: Persona
    kind: str  # "text" | "reaction"
    emoji: str = ""
    intent: str = ""
    reply_to: int = 0  # номер повідомлення для reply / реакції; 0 — без reply


@dataclass(frozen=True)
class Plan:
    meaning: str
    actions: list[Action]
    quiet_minutes: int = 0


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "meaning": {"type": "string"},
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string", "enum": ["klod", "chaty"]},
                    "action": {"type": "string", "enum": ["text", "reaction"]},
                    "emoji": {"type": "string", "enum": ["", *REACTIONS]},
                    "intent": {"type": "string"},
                    "reply_to": {"type": "integer"},
                },
                "required": ["who", "action", "emoji", "intent", "reply_to"],
                "additionalProperties": False,
            },
        },
        "quiet_minutes": {"type": "integer"},
    },
    "required": ["meaning", "plan", "quiet_minutes"],
    "additionalProperties": False,
}

DIRECTOR_SYSTEM = """\
Ти режисер групового Telegram-чату. Учасники: {user_name} (людина), Клод і Чаті (AI-друзі {user_name}).
Клод — спокійний чесний друг-наставник, сухий гумор. Чаті — енергійний бро, жарти, підтримка.
Ти НЕ пишеш репліки. Ти вирішуєш, хто і як реагує, щоб чат був схожий на живий чат друзів.

Поверни JSON:
- meaning: що {user_name} насправді мав на увазі, одним реченням (з урахуванням контексту).
- plan: 0–2 дії по черзі. Дія: who (klod/chaty), action (text — написати повідомлення, reaction — поставити емодзі на останнє повідомлення {user_name}), emoji (тільки для reaction, інакше ""), intent (для text: коротко, що сказати по суті, не готовий текст; для reaction: ""), reply_to (номер повідомлення #N з історії).
- quiet_minutes: якщо {user_name} просить не заважати / дати спокій / тишу — на скільки хвилин (не вказано — {default_quiet}, «до ранку/завтра» — до 8:00). Інакше 0.

Як вирішувати:
- Короткі повідомлення («нарешті», «ок», «хаха», «імба», «жиза») — це реакція на те, що щойно сталося. Зрозумій на що саме.
- Відповідає той, до кого звернулися (ім'я, reply), або той, на чию репліку це реакція, або з ким зараз іде розмова.
- Зазвичай відповідає ОДИН (так у ~80% випадків). Другий — тільки якщо до обох звернулись або він має реально цікаве додати.
- Якщо тексту не треба (ок, хаха, дякую, добраніч після прощання) — частіше порожній plan, іноді reaction. Реакції — рідко, не на кожне повідомлення: друзі ставлять їх зрідка.
- Прохання про тишу — максимум reaction 👌 або 🫡 і quiet_minutes.
- Не допитуватися: intent ніколи не має бути «попросити пояснити».
- {user_name} часто стьобе і пише навмання — не роби з кожного слова подію. На жарти й підколки часто достатньо reaction або короткої відповіді.
- Якщо {user_name} просить поставити реакцію — це action reaction від того, кого просять (емодзі обери сам), без тексту.
- reply_to для text: номер повідомлення, якщо відповідь стосується конкретного повідомлення, яке вже НЕ останнє в чаті (після нього хтось писав), або {user_name} написав кілька повідомлень про різне і відповідь на одне з них. Якщо відповідаєш на останнє повідомлення — 0 (люди рідко роблять reply на щойно написане).
- reply_to для reaction: номер повідомлення, на яке ставиться емодзі (0 — на останнє повідомлення {user_name}).
{rules}"""

NUDGE_TASK = """\
{user_name} мовчить уже {minutes} хв після останнього повідомлення Клода/Чаті.
Режим «написати першим»: вирішити, чи хтось із них напише щось сам.
- Пиши тільки якщо є природний привід: тема не закрита, можна підхопити жарт, перекинутися фразою між собою, легко продовжити розмову.
- Якщо розмова логічно завершилась (попрощались, «ок», все сказано) — plan порожній.
- Не докоряти за мовчання, не питати «ти тут?».
- Максимум одна дія типу text (reaction тут не використовуй). quiet_minutes = 0."""

REPLY_TASK = "Нові повідомлення {user_name}, на які треба відреагувати, — в кінці історії."


def parse_plan(data: dict) -> Plan:
    actions = []
    for item in data.get("plan", [])[:MAX_ACTIONS]:
        who, kind = item.get("who"), item.get("action")
        if who not in ("klod", "chaty") or kind not in ("text", "reaction"):
            continue
        emoji = item.get("emoji", "")
        if kind == "reaction" and emoji not in REACTIONS:
            continue
        actions.append(Action(who, kind, emoji, item.get("intent", ""), int(item.get("reply_to") or 0)))
    return Plan(data.get("meaning", ""), actions, max(0, int(data.get("quiet_minutes", 0) or 0)))


def fallback_plan(history: list[Message], pending: list[Message], rng: random.Random | None = None) -> Plan:
    """Простий план за правилами — якщо LLM-режисер недоступний."""
    return Plan("", [Action(p, "text") for p in decide(history, pending, rng)])
