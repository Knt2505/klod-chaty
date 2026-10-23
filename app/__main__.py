import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import load_config
from .db import Database
from .engine import Engine
from .llm import LLM
from .telegram import TelegramSender, build_router


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()

    db = Database(cfg.db_path)
    await db.connect()

    klod_bot = Bot(cfg.klod_token)
    chaty_bot = Bot(cfg.chaty_token)
    klod_me = await klod_bot.get_me()
    chaty_me = await chaty_bot.get_me()
    logging.info("Клод = @%s, Чаті = @%s", klod_me.username, chaty_me.username)

    engine = Engine(
        db,
        LLM(cfg),
        TelegramSender({"klod": klod_bot, "chaty": chaty_bot}),
        timezone=cfg.timezone,
        debounce_seconds=cfg.debounce_seconds,
        history_limit=cfg.history_limit,
    )
    dp = Dispatcher()
    dp.include_router(build_router(engine, cfg, {klod_me.id: "klod", chaty_me.id: "chaty"}))

    try:
        await dp.start_polling(klod_bot, allowed_updates=["message"])
    finally:
        await chaty_bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
