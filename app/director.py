"""Режисер, етап 1: прості правила, хто відповідає.

На етапі 3 його замінить LLM-режисер (вклинювання, реакції, мовчання).
"""

import random
import re

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
