import asyncio
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db import Database
from app.engine import Engine
from app.transcript import clean_reply


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.plans = []  # що поверне режисер; порожньо — режисер «падає» і працюють прості правила
        self.director_prompts = []

    async def direct(self, system, prompt, schema):
        self.director_prompts.append((system, prompt))
        if not self.plans:
            raise RuntimeError("no director in this test")
        return self.plans.pop(0)

    async def generate(self, persona, system, prompt, images):
        self.calls.append((persona, system, prompt, images))
        return f"{'Клод' if persona == 'klod' else 'Чаті'}: відповідь {len(self.calls)}"


class FakeSender:
    def __init__(self):
        self.sent = []
        self.reactions = []
        self.replies = []

    async def typing(self, chat_id, persona):
        pass

    async def send(self, chat_id, persona, text, reply_to=None):
        self.sent.append((persona, text))
        self.replies.append(reply_to)
        return 1000 + len(self.sent)

    async def react(self, chat_id, persona, message_id, emoji):
        self.reactions.append((persona, message_id, emoji))

    async def download(self, file_id):
        return f"bytes-{file_id}".encode()


@pytest.fixture
async def setup(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.connect()
    llm, sender = FakeLLM(), FakeSender()
    engine = Engine(db, llm, sender, timezone="America/Chicago", debounce_seconds=0.05,
                    history_limit=30, rng=random.Random(1), typing_pace=0, nudge_minutes=0,
                    clock=lambda: datetime(2026, 10, 23, 14, 0, tzinfo=ZoneInfo("America/Chicago")))
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
    cfg = Config(
        klod_token="k", chaty_token="c", anthropic_api_key="a", openai_api_key="o",
        owner_id=None, chat_id=None, klod_model="m1", director_model="m3", chaty_model="m2",
        chaty_reasoning_effort=None, db_path="x", timezone="America/Chicago",
        debounce_seconds=1, history_limit=30, nudge_minutes=5,
    )
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
    assert humanize(text) == ["клод тут. чаті, не мовчи", "ден, імба щодо чого? скажи що"]
    long = ("ден, ключ від мене в полі для чаті це не жарт. вважай його засвіченим. "
            "відкликай у кабінеті і згенеруй новий. потім глянь конфіг.")
    assert humanize(long) == [
        "ден, ключ від мене в полі для чаті це не жарт",
        "вважай його засвіченим",
        "відкликай у кабінеті і згенеруй новий",
        "потім глянь конфіг",
    ]
    assert humanize("ок, чесно — тут «так» і все") == ["ок, чесно, тут так і все"]
    assert humanize("ПОВАЖАЮ. API впало...") == ["ПОВАЖАЮ. API впало..."]
    assert humanize("а\n\nб\n\nв\n\nг\n\nґ\n\nд") == ["а", "б", "в", "г", "ґ д"]
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


async def test_mentioned_persona_answers_once(setup):
    engine, db, llm, sender = setup
    replies = iter(["чаті, а ти шо думаєш?", "клод, я думаю нормально", "ще щось"])

    async def scripted(persona, system, prompt, images):
        llm.calls.append(persona)
        return next(replies)

    llm.generate = scripted
    await engine.on_user_message(1, "Клод, привіт", None, None, "Ден")
    await asyncio.sleep(0.2)
    # Клод покликав Чаті — той відповів; Чаті згадав Клода, але Клод уже говорив у цьому раунді.
    assert [p for p, _ in sender.sent] == ["klod", "chaty"]


def plan(*actions, meaning="m", quiet=0):
    return {"meaning": meaning, "quiet_minutes": quiet, "plan": [
        {"who": who, "action": kind, "emoji": emoji, "intent": intent, "reply_to": reply_to}
        for who, kind, emoji, intent, *rest in actions
        for reply_to in [rest[0] if rest else 0]
    ]}


async def test_director_reaction_and_text(setup):
    engine, db, llm, sender = setup
    llm.plans = [plan(("chaty", "reaction", "🔥", ""), ("klod", "text", "", "порадій разом"))]
    await engine.on_user_message(1, "нарешті", 77, None, "Ден")
    await asyncio.sleep(0.2)
    assert sender.reactions == [("chaty", 77, "🔥")]
    assert [p for p, _ in sender.sent] == ["klod"]
    assert "Що тобі варто зробити: порадій разом" in llm.calls[0][2]
    assert "Що відбувається: m" in llm.calls[0][2]


async def test_director_can_stay_silent_and_invalid_emoji_dropped(setup):
    engine, db, llm, sender = setup
    llm.plans = [plan(("klod", "reaction", "🦄", ""))]
    await engine.on_user_message(1, "ок", 5, None, "Ден")
    await asyncio.sleep(0.2)
    assert sender.sent == [] and sender.reactions == []


async def test_quiet_request_blocks_nudge_until_user_writes(setup):
    engine, db, llm, sender = setup
    llm.plans = [plan(("klod", "reaction", "👌", ""), quiet=60)]
    await engine.on_user_message(1, "не мішайте", 5, None, "Ден")
    await asyncio.sleep(0.2)
    assert await engine.is_quiet(1)
    await db.add_message(1, "klod", "йо")
    await engine.nudge(1)
    assert sender.sent == []
    await engine.on_user_message(1, "я тут", 6, None, "Ден")
    assert not await engine.is_quiet(1)


async def test_nudge_once_after_silence(setup):
    engine, db, llm, sender = setup
    engine.nudge_minutes = 0.002  # ~0.1 с
    llm.plans = [plan(("klod", "text", "", "відповісти")), plan(("chaty", "text", "", "підхопити тему"))]
    await engine.on_user_message(1, "Клод, привіт", 5, None, "Ден")
    await asyncio.sleep(0.5)
    assert [p for p, _ in sender.sent] == ["klod", "chaty"]
    assert "мовчить" in llm.director_prompts[1][1]
    await asyncio.sleep(0.3)
    assert len(sender.sent) == 2  # другий раз без нового повідомлення не пише


async def test_no_nudge_at_night(setup):
    engine, db, llm, sender = setup
    engine.clock = lambda: datetime(2026, 10, 23, 2, 30, tzinfo=ZoneInfo("America/Chicago"))
    await db.add_message(1, "klod", "йо")
    await engine.nudge(1)
    assert llm.director_prompts == []


async def test_rules_reach_prompts(setup):
    engine, db, llm, sender = setup
    await db.add_rule("не допитуйся")
    llm.plans = [plan(("klod", "text", "", "x"))]
    await engine.on_user_message(1, "йо", 5, None, "Ден")
    await asyncio.sleep(0.2)
    assert "1. не допитуйся" in llm.calls[0][1]
    assert "1. не допитуйся" in llm.director_prompts[0][0]
    assert await db.delete_rule(1) == "не допитуйся" and await db.rules() == []


async def test_new_user_message_interrupts_round(setup):
    engine, db, llm, sender = setup
    llm.plans = [plan(("klod", "text", "", "a"), ("chaty", "text", "", "b"))]
    original_send = sender.send

    async def send_and_user_types(chat_id, persona, text, reply_to=None):
        engine._pending.setdefault(chat_id, []).append("нове повідомлення")
        return await original_send(chat_id, persona, text, reply_to)

    sender.send = send_and_user_types
    await engine.on_user_message(1, "йо", 5, None, "Ден")
    await asyncio.sleep(0.2)
    assert [p for p, _ in sender.sent] == ["klod"]  # Чаті не встряв, бо Ден пише далі


async def test_second_speaker_is_told_to_be_brief(setup):
    engine, db, llm, sender = setup
    llm.plans = [plan(("klod", "text", "", "a"), ("chaty", "text", "", "жарт"))]
    await engine.on_user_message(1, "йо", 5, None, "Ден")
    await asyncio.sleep(0.2)
    assert "жарт. Коротко, одним повідомленням." in llm.calls[1][2]


async def test_reply_to_known_message_only(setup):
    engine, db, llm, sender = setup
    llm.plans = [plan(("klod", "text", "", "про перше", 10), ("chaty", "text", "", "x", 999))]
    await engine.on_user_message(1, "перше", 10, None, "Ден")
    await engine.on_user_message(1, "друге", 11, None, "Ден")
    await asyncio.sleep(0.2)
    assert sender.replies == [10, None]  # 999 нема в історії — reply прибрано
    assert "#10 " in llm.director_prompts[0][1]


def test_humanize_splits_only_sometimes_with_rng():
    from app.style import humanize

    text = "ок, це вже норм тема. давай спробуємо завтра зранку"
    always = random.Random(0)
    always.random = lambda: 0.0
    never = random.Random(0)
    never.random = lambda: 0.99
    assert len(humanize(text, always)) == 2
    assert humanize(text, never) == ["ок, це вже норм тема. давай спробуємо завтра зранку"]
    very_long = "дуже довге речення про все на світі і ще трохи. " * 4
    assert len(humanize(very_long, never)) > 1
