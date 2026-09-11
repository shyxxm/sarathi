"""SPEC 7.2: code's words to a driver — one table, one language per utterance."""

from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.api.service import ShiftService
from app.contracts.enums import Intent, Language, ReplyMode
from app.contracts.event import InterpreterOutput
from app.domain import words


def finished_malayalam():
    """A stand-in for the table once it is written: every line, marked."""
    return {key: f"[ml] {template}" for key, template in words.EN.items()}


def escalate_unreadable(shift):
    shift.interpret = lambda text: InterpreterOutput(
        intents=[Intent.REPORT], language=Language.ML, transcript_legible=False)
    shift.submit("sdkjfh skdjfh", str(uuid4()))
    return shift.exchanges[-1].reply


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


def test_an_unfinished_table_is_never_half_spoken():
    assert words.missing(Language.ML), "the Malayalam lines are still to write"
    assert words.spoken(Language.ML) is Language.EN
    assert words.spoken(Language.EN) is Language.EN
    assert words.say(Language.ML, "fact.now", time="12:02") == "It is now 12:02."


def test_until_malayalam_is_written_an_escalation_is_all_english():
    reply = escalate_unreadable(ShiftService())
    assert reply.mode is ReplyMode.ESCALATE and reply.language is Language.EN
    assert reply.text.startswith("Recorded: I could not make out")
    assert "ഓഫീസ" not in reply.text


def test_once_it_is_written_an_escalation_is_all_his_language(monkeypatch):
    """The case this table exists for: a Malayalam speaker who escalates, when
    he most needs to understand what is happening, heard English end to end."""
    monkeypatch.setitem(words.TABLES, Language.ML, finished_malayalam())
    reply = escalate_unreadable(ShiftService())
    assert reply.language is Language.ML
    assert reply.text.startswith("[ml] Recorded: [ml] I could not make out")
    assert "[ml] Someone at the office" in reply.text
    assert all(fact.startswith("[ml]") for fact in reply.restated_facts)


def test_the_dispatcher_keeps_english_while_the_driver_hears_malayalam(monkeypatch):
    monkeypatch.setitem(words.TABLES, Language.ML, finished_malayalam())
    with TestClient(create_app(ShiftService())) as client:
        board, driver = client.get("/dispatcher").text, client.get("/driver").text
    assert "The gate is closed" in board and "[ml]" not in board
    assert "[ml] The gate is closed" in driver
