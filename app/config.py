import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    klod_token: str
    chaty_token: str
    anthropic_api_key: str
    openai_api_key: str
    owner_id: int | None
    chat_id: int | None
    klod_model: str
    director_model: str
    chaty_model: str
    chaty_reasoning_effort: str | None
    db_path: str
    timezone: str
    debounce_seconds: float
    history_limit: int
    nudge_minutes: float


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задано змінну оточення {name} (див. .env.example)")
    return value


def load_config() -> Config:
    load_dotenv()
    return Config(
        klod_token=_required("KLOD_BOT_TOKEN"),
        chaty_token=_required("CHATY_BOT_TOKEN"),
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        openai_api_key=_required("OPENAI_API_KEY"),
        owner_id=_optional_int("OWNER_ID"),
        chat_id=_optional_int("CHAT_ID"),
        klod_model=os.getenv("KLOD_MODEL", "claude-haiku-5-5"),
        director_model=os.getenv("DIRECTOR_MODEL", "claude-haiku-5-5"),
        chaty_model=os.getenv("CHATY_MODEL", "gpt-5.4-mini"),
        chaty_reasoning_effort=os.getenv("CHATY_REASONING_EFFORT", "none").strip() or None,
        db_path=os.getenv("DB_PATH", "data/klod_chaty.db"),
        timezone=os.getenv("TIMEZONE", "America/Chicago"),
        debounce_seconds=float(os.getenv("DEBOUNCE_SECONDS", "3")),
        history_limit=int(os.getenv("HISTORY_LIMIT", "30")),
        nudge_minutes=float(os.getenv("NUDGE_MINUTES", "5")),
    )
