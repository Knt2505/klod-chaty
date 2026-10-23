"""Людський вигляд повідомлень: маленькі літери, без абзаців, без крапки в кінці."""

import random
import re

MAX_PARTS = 5
MAX_PART_LENGTH = 50  # довше — можна різати по реченнях на окремі повідомлення
ALWAYS_SPLIT_LENGTH = 140  # а таке довге — ріжемо завжди
SPLIT_CHANCE = 0.4  # як часто ділимо середні повідомлення, щоб не було шаблонно

_PARAGRAPHS = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
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


_QUOTES = re.compile(r"[\"«»“”„]")
_DASH = re.compile(r"\s*[—–]\s*")


def _plain_punctuation(text: str) -> str:
    """Без лапок і довгих тире — люди в чаті так не пишуть, це видає ШІ."""
    return _DASH.sub(", ", _QUOTES.sub("", text)).replace(", ,", ",")


def _split_long(paragraph: str) -> list[str]:
    """Довгий абзац -> кілька коротких повідомлень, по цілих реченнях."""
    chunks: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        if chunks and len(chunks[-1]) + 1 + len(sentence) <= MAX_PART_LENGTH:
            chunks[-1] += " " + sentence
        else:
            chunks.append(sentence)
    return chunks


def humanize(text: str, rng: random.Random | None = None) -> list[str]:
    """Розбиває відповідь на 1–5 повідомлень і робить їх схожими на людські.

    Без rng середні абзаци ділимо завжди (детерміновано, для тестів);
    з rng — лише іноді, як людина, яка то пише одним, то кількома повідомленнями.
    """
    paragraphs = [" ".join(p.split()) for p in _PARAGRAPHS.split(text.strip())]
    parts = []
    for p in paragraphs:
        if not p:
            continue
        split = len(p) > ALWAYS_SPLIT_LENGTH or rng is None or rng.random() < SPLIT_CHANCE
        parts.extend(_split_long(p) if split else [p])
    if len(parts) > MAX_PARTS:
        parts = parts[: MAX_PARTS - 1] + [" ".join(parts[MAX_PARTS - 1 :])]
    return [_drop_final_period(_lower_sentence_starts(_plain_punctuation(p))) for p in parts]
