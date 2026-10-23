import random
from datetime import datetime, timezone

from app import director
from app.models import Message

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)


def msg(author, text, reply_to=None):
    return Message(author, text, NOW, reply_to_author=reply_to)


def test_name_mention_picks_that_persona():
    pending = [msg("user", "клоде, шо думаєш?")]
    assert director.decide(pending, pending) == ["klod"]


def test_both_names_in_order_of_appearance():
    pending = [msg("user", "Чаті і Клод, розсудіть нас")]
    assert director.decide(pending, pending) == ["chaty", "klod"]


def test_in_chat_is_not_a_mention():
    assert director.mentioned("я це вже писав у чаті") == []
    assert director.mentioned("Чаті, ти тут?") == ["chaty"]


def test_reply_wins_over_name():
    pending = [msg("user", "клод не правий", reply_to="chaty")]
    assert director.decide(pending, pending) == ["chaty"]


def test_continues_with_last_speaker_without_switch():
    history = [msg("user", "привіт"), msg("klod", "привіт"), msg("user", "як справи")]
    rng = random.Random()
    rng.random = lambda: 0.9  # без перемикання
    assert director.decide(history, history[-1:], rng) == ["klod"]


def test_sometimes_switches_to_other():
    history = [msg("user", "привіт"), msg("klod", "привіт"), msg("user", "як справи")]
    rng = random.Random()
    rng.random = lambda: 0.1
    assert director.decide(history, history[-1:], rng) == ["chaty"]


def test_no_pending_no_reply():
    assert director.decide([msg("klod", "йо")], []) == []
