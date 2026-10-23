from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Author = Literal["user", "klod", "chaty"]
Persona = Literal["klod", "chaty"]

PERSONAS: tuple[Persona, ...] = ("klod", "chaty")
DISPLAY_NAMES: dict[str, str] = {"klod": "Клод", "chaty": "Чаті"}


@dataclass(frozen=True)
class Message:
    author: Author
    text: str
    created_at: datetime
    tg_id: int | None = None
    reply_to_author: Author | None = None
