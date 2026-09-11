"""SPEC 7.2: code's words to a driver — one table, one language per sentence.

The behaviour tests build their own Malayalam table, so they say the same
thing however much of the real one is written.
"""

from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.api.service import ShiftService
from app.contracts.enums import Intent, Language, ReplyMode
from app.contracts.event import InterpreterOutput
from app.domain import words


def table(*keys):
    """A Malayalam table with only these lines written, each marked so a test
    can see which language every sentence came out in."""
    return {key: words.TODO for key in words.EN} | {key: f"[ml] {words.EN[key]}" for key in keys}


def escalate_unreadable(shift):
    shift.interpret = lambda text: InterpreterOutput(
        intents=[Intent.REPORT], language=Language.ML, transcript_legible=False)
    shift.submit("sdkjfh skdjfh", str(uuid4()))
    return shift.exchanges[-1].reply


# --- the real table ------------------------------------------------------------

def test_malayalam_lists_every_line_english_has_and_nothing_else():
    assert set(words.ML) == set(words.EN)
    assert set(words.MIXED) <= set(words.EN) and set(words.HI) <= set(words.EN)


@pytest.mark.parametrize("language", list(words.TABLES))
def test_a_written_line_uses_only_slots_the_english_offers(language):
    """A translation may drop a slot. It may not invent one — that would fail
    at the moment he is spoken to."""
    for key, template in words.TABLES[language].items():
        if template != words.TODO:
            assert words.slots(template) <= words.slots(words.EN[key]), key


def test_malayalam_never_quotes_his_question_back():
    """The slot carries the interpreter's English, and an English clause inside
    a Malayalam sentence is the mixing problem again."""
    assert "question" not in words.slots(words.ML["fact.asked"])


def test_every_unreviewed_line_is_written_and_listed():
    """Placeholders written without a Malayalam speaker stay on this list until
    one has checked them, so nobody takes them for reviewed text."""
    written = {key for key, line in words.ML.items() if line != words.TODO}
    assert words.UNREVIEWED <= written


# --- one sentence, one language ---------------------------------------------------

def test_a_sentence_is_his_language_or_english_never_half_of_each(monkeypatch):
    monkeypatch.setitem(words.TABLES, Language.ML, table("fact.now", "fact.stop"))
    now = words.say(Language.ML, "fact.now", time="12:02")
    assert now == "[ml] It is now 12:02." and now.language is Language.ML
    # The frame is written and the status it carries is not: the whole
    # sentence is English, not a Malayalam frame around an English status.
    stop = words.say(Language.ML, "fact.stop", seq=3, customer="Malabar Electrical Supplies",
                     status=words.part("status.ARRIVED"))
    assert stop == "Stop 3, Malabar Electrical Supplies: arrived, unloading not started."
    assert stop.language is Language.EN
    assert words.say(Language.ML, "fact.window", open="11:30", close="13:00") == "Delivery window 11:30–13:00."


def test_an_escalation_with_an_unwritten_fact_says_its_facts_in_english(monkeypatch):
    """"Recorded: a; b." is one sentence, and it waits for its facts. The
    closing is a sentence of its own."""
    monkeypatch.setitem(words.TABLES, Language.ML, table("escalation.opening", "escalation.closing"))
    reply = escalate_unreadable(ShiftService())
    assert reply.mode is ReplyMode.ESCALATE
    assert reply.text == ("Recorded: I could not make out what happened or which stop this is about. "
                          "[ml] Someone at the office is checking the rest now.")


def test_one_written_line_is_heard_at_once(monkeypatch):
    """Per line, not all or nothing, so the lines he hears most can be written
    carefully first. Here the unclear-message line is written."""
    monkeypatch.setitem(words.TABLES, Language.ML,
                        table("escalation.opening", "escalation.closing", "issue.unclear"))
    reply = escalate_unreadable(ShiftService())
    assert reply.text == ("[ml] Recorded: [ml] I could not make out what happened or which stop this is about. "
                          "[ml] Someone at the office is checking the rest now.")
    assert reply.language is Language.ML


def test_once_it_is_all_written_an_escalation_is_all_his_language(monkeypatch):
    """The case the table exists for: a Malayalam speaker who escalates, when
    he most needs to understand what is happening, heard English end to end."""
    monkeypatch.setitem(words.TABLES, Language.ML, table(*words.EN))
    reply = escalate_unreadable(ShiftService())
    assert reply.language is Language.ML
    assert reply.text.startswith("[ml] Recorded: [ml] I could not make out")
    assert all(fact.startswith("[ml]") for fact in reply.restated_facts)


def test_the_dispatcher_keeps_english_while_the_driver_hears_malayalam(monkeypatch):
    monkeypatch.setitem(words.TABLES, Language.ML, table(*words.EN))
    with TestClient(create_app(ShiftService())) as client:
        board, driver = client.get("/dispatcher").text, client.get("/driver").text
    assert "The gate is closed" in board and "[ml]" not in board
    assert "[ml] The gate is closed" in driver
