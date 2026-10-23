"""Людський вигляд повідомлень: маленькі літери, без абзаців, без крапки в кінці."""

import re

MAX_PARTS = 3

_PARAGRAPHS = re.compile(r"\n\s*\n")
# Початок повідомлення або початок речення після . ! ? …
_SENTENCE_START = re.compile(r"(^|[.!?…]\s+)(\w)(\w*)")


def _lower_sentence_starts(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        first, rest = match.group(2), match.group(3)
        # «API», «ОК», «ПОВАЖАЮ» не чіпаємо — тільки слова з великої літери.
        if rest and not rest.islower():
            return match.group(0)
        return match.group(1) + first.lower() + rest

    return _SENTENCE_START.sub(repl, text)


def _drop_final_period(text: str) -> str:
    if text.endswith(".") and not text.endswith(".."):
        return text[:-1]
    return text


def humanize(text: str) -> list[str]:
    """Розбиває відповідь на 1–3 повідомлення і робить їх схожими на людські."""
    parts = [" ".join(p.split()) for p in _PARAGRAPHS.split(text.strip())]
    parts = [p for p in parts if p]
    if len(parts) > MAX_PARTS:
        parts = parts[: MAX_PARTS - 1] + [" ".join(parts[MAX_PARTS - 1 :])]
    return [_drop_final_period(_lower_sentence_starts(p)) for p in parts]
