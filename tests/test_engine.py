import asyncio
import random

import pytest

from app.db import Database
from app.engine import Engine
from app.transcript import clean_reply


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, persona, system, prompt, images):
        self.calls.append((persona, system, prompt, images))
        return f"{'Клод' if persona == 'klod' else 'Чаті'}: відповідь {len(self.calls)}"


class FakeSender:
    def __init__(self):
        self.sent = []

    async def typing(self, chat_id, persona):
        pass

    async def send(self, chat_id, persona, text):
        self.sent.append((persona, text))
        return 1000 + len(self.sent)

    async def download(self, file_id):
        return f"bytes-{file_id}".encode()


@pytest.fixture
async def setup(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.connect()
    llm, sender = FakeLLM(), FakeSender()
    engine = Engine(db, llm, sender, timezone="America/Chicago", debounce_seconds=0.05,
                    history_limit=30, rng=random.Random(1), typing_pace=0)
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


async def test_binding_persists_and_migrates(setup, tmp_path):
    from app.config import Config
    from app.telegram import Binding

    _, db, _, _ = setup
    cfg = Config("k", "c", "a", "o", None, None, "m1", "m2", None, "x", "America/Chicago", 1, 30)
    binding = await Binding.load(cfg, db)
    assert not binding.bound
    await binding.save(db, -100, 42)
    await db.add_message(-100, "user", "йо")
    assert (await Binding.load(cfg, db)).chat_id == -100

    await db.move_chat(-100, -200)
    await binding.save(db, -200, 42)
    reloaded = await Binding.load(cfg, db)
    assert (reloaded.chat_id, reloaded.owner_id) == (-200, 42)
    assert [m.text for m in await db.recent(-200, 5)] == ["йо"]


def test_humanize():
    from app.style import humanize

    text = "Клод тут. Чаті, не мовчи.\n\nДен, \"імба\" щодо чого? Скажи що."
    assert humanize(text) == ["клод тут. чаті, не мовчи", "ден, \"імба\" щодо чого? скажи що"]
    assert humanize("ПОВАЖАЮ. API впало...") == ["ПОВАЖАЮ. API впало..."]
    assert humanize("а\n\nб\n\nв\n\nг") == ["а", "б", "в г"]
    assert humanize("рядок\nще рядок") == ["рядок ще рядок"]


async def test_only_last_two_photos_are_attached(setup):
    engine, db, llm, sender = setup
    for i in range(3):
        await engine.on_user_message(1, "[фото]", None, None, "Ден", image_file_id=f"p{i}")
    await engine.on_user_message(1, "Клод, глянь", None, None, "Ден")
    await asyncio.sleep(0.2)
    _, _, prompt, images = llm.calls[0]
    assert images == [b"bytes-p1", b"bytes-p2"]
    assert "старе фото" in prompt and "фото №2, прикріплене" in prompt
    history = await db.recent(1, 10)
    assert history[0].image_file_id == "p0"


async def test_retry_without_images_on_failure(setup):
    engine, db, llm, sender = setup
    original = llm.generate

    async def flaky(persona, system, prompt, images):
        if images:
            raise RuntimeError("vision failed")
        return await original(persona, system, prompt, images)

    llm.generate = flaky
    await engine.on_user_message(1, "Клод, глянь", None, None, "Ден", image_file_id="p")
    await asyncio.sleep(0.2)
    assert [p for p, _ in sender.sent] == ["klod"]
    assert "прикріплене" not in llm.calls[0][2]
