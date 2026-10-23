import asyncio
import random

import pytest

from app.db import Database
from app.engine import Engine
from app.transcript import clean_reply


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, persona, system, prompt):
        self.calls.append((persona, system, prompt))
        return f"{'Клод' if persona == 'klod' else 'Чаті'}: відповідь {len(self.calls)}"


class FakeSender:
    def __init__(self):
        self.sent = []

    async def typing(self, chat_id, persona):
        pass

    async def send(self, chat_id, persona, text):
        self.sent.append((persona, text))
        return 1000 + len(self.sent)


@pytest.fixture
async def setup(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.connect()
    llm, sender = FakeLLM(), FakeSender()
    engine = Engine(db, llm, sender, timezone="America/Chicago", debounce_seconds=0.05,
                    history_limit=30, rng=random.Random(1))
    yield engine, db, llm, sender
    await db.close()


async def test_burst_of_messages_gets_one_reply(setup):
    engine, db, llm, sender = setup
    for text in ("Клод", "слухай", "я знову не пішов у зал"):
        await engine.on_user_message(1, text, None, None, "Влад")
    await asyncio.sleep(0.2)
    assert [p for p, _ in sender.sent] == ["klod"]
    assert sender.sent[0][1] == "відповідь 1"  # префікс імені прибрано
    prompt = llm.calls[0][2]
    assert "Влад: я знову не пішов у зал" in prompt
    assert "Влад" in llm.calls[0][1]


async def test_both_reply_and_second_sees_first(setup):
    engine, db, llm, sender = setup
    await engine.on_user_message(1, "Клод, Чаті, що скажете?", None, None, "Влад")
    await asyncio.sleep(0.2)
    assert [p for p, _ in sender.sent] == ["klod", "chaty"]
    assert "Клод: відповідь 1" in llm.calls[1][2]
    history = await db.recent(1, 10)
    assert [m.author for m in history] == ["user", "klod", "chaty"]


def test_clean_reply():
    assert clean_reply("[Пт 18:02] Чаті: йо") == "йо"
    assert clean_reply("Клод: ок") == "ок"
    assert clean_reply("клод правий") == "клод правий"
