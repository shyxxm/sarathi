from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import tracing
from app.agents import responder, safety_critic
from app.agents.interpreter import InterpreterError
from app.api.main import create_app
from app.api.service import MessageUnavailable, ShiftService, waiting_at
from app.api.views import context
from app.domain import detention
from app.contracts.enums import EventType, Intent, Language, ReplyMode
from app.contracts.event import InterpreterOutput
from app.contracts.reply import Claim, ResponderOutput
from app.contracts.retrieval import RetrievalResult, RetrievedChunk


@pytest.fixture
def service():
    return ShiftService()


@pytest.fixture
def client(service):
    with TestClient(create_app(service)) as client:
        yield client


def interpret_as(monkeypatch, service, event_type, intents=(Intent.REPORT,), **kwargs):
    monkeypatch.setattr(service, "interpret", lambda text: InterpreterOutput(
        intents=list(intents), language=Language.EN, event_type=event_type, **kwargs))


def capture_context(monkeypatch):
    """The exact text the responder was handed. It is the artefact you inspect
    when a reply comes out wrong, so it is what these assert against."""
    rendered = []
    original = responder.assemble

    def recording(**kwargs):
        context = original(**kwargs)
        rendered.append(context)
        return context

    monkeypatch.setattr(responder, "assemble", recording)
    return rendered


def post(client, text, message_id=None, *, htmx=True):
    return client.post("/driver/messages", data={"text": text, "message_id": message_id or str(uuid4())},
                       headers={"HX-Request": "true"} if htmx else {})


def stub_rules(monkeypatch, service, *, supported=True):
    source = RetrievedChunk(id="SOP-TEST-001", customer_id="customer-2",
                            source_document="standing-instructions.md",
                            text="Sixty minutes free. <script>alert('source')</script>", score=.8)
    monkeypatch.setattr(service, "retrieve", lambda *args: RetrievalResult(sop_chunks=(source,), relevance_floor=.6))
    claim = Claim(text="You have 60 minutes free.", cited_chunk_id=source.id)
    monkeypatch.setattr(responder, "respond", lambda ctx: ResponderOutput(
        language=Language.EN, text="The gate is closed. You have 60 minutes free.",
        restated_facts=["The gate is closed at stop 2."], claims=[claim], confidence=.9))
    monkeypatch.setattr(safety_critic, "ground", lambda *args, **kwargs: safety_critic.GroundingOutput(
        verdicts=[safety_critic.Verdict(supported=supported, affects_pay_or_liability=True,
                                     reason="The passage does not support this rule." if not supported else "")]))
    return source


def test_surfaces_and_no_audio_capture(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/driver"
    html = client.get("/driver").text
    assert 'name="text"' in html and "Send to Sarathi" in html
    assert "Your day, recorded" in html
    assert "<audio" not in html and "microphone" not in html
    html = client.get("/dispatcher").text
    assert all(name in html for name in ["NEEDS ATTENTION", "HANDLED", "RUNNING FINE"])
    assert 'min="0" max="600"' in html
    assert "System-observed wait" in html and "Driver-claimed wait" in html
    assert "Not assessed" not in html and "Model self-rating" in html
    assert "Captured model check" in html and "Weakest: SOP retrieval" in html
    assert client.get("/static/vendor/htmx.min.js").status_code == 200


def test_replay_is_reversible_and_never_changes_current_shift(client, service):
    before = service.state
    for minute in [600, 0, 193, 194, 193]:
        response = client.get(f"/dispatcher/board?minute={minute}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "<!doctype html>" not in response.text
    assert service.state is before
    assert service.minute == 193
    assert not service.replay(0).events
    assert service.replay(193) == service.snapshots[193]
    assert client.get("/dispatcher?minute=-1").status_code == 422
    assert client.get("/dispatcher?minute=601").status_code == 422
    assert client.get("/dispatcher?minute=oops").status_code == 422


def test_wait_uses_ingestion_and_freezes_when_unloading_starts(service):
    gate = waiting_at(service.replay(193), "stop-2", service.now)
    assert (gate.observed_wait_minutes, gate.driver_claimed_wait_minutes) == (61, 8)
    assert (gate.free_detention_minutes, gate.billable_minutes, gate.exposure_paise) == (60, 1, 150)
    for minute in [194, 600]:
        state = service.replay(minute)
        waiting = waiting_at(state, "stop-2", state.shift_end)
        assert waiting.observed_wait_minutes == 62
        assert waiting.exposure_paise == 300


def test_report_uses_state_machine_and_duplicate_submission_is_idempotent(client, service, monkeypatch):
    interpret_as(monkeypatch, service, EventType.SERVICE_STARTED)
    message_id = str(uuid4())
    count = len(service.state.events)
    for _ in range(2):
        response = post(client, "The gate opened and unloading started.", message_id)
        assert response.status_code == 200
        assert 'hx-swap-oob="outerHTML"' in response.text
    assert len(service.state.events) == count + 1
    assert len(service.exchanges) == 1
    assert service.state.stop("stop-2").status.value == "IN_SERVICE"
    assert not context(service)["attention"]


def test_rejected_transition_keeps_status_and_does_not_claim_it_was_recorded(client, service, monkeypatch):
    interpret_as(monkeypatch, service, EventType.ARRIVED_STOP)
    response = post(client, "I have arrived again.")
    assert response.status_code == 200
    assert service.state.stop("stop-2").status.value == "ARRIVED"
    assert service.state.events[-1].event_type is EventType.UNCLEAR
    assert service.exchanges[-1].reply.mode is ReplyMode.ESCALATE
    assert "I already have" in service.exchanges[-1].reply.text


def test_failed_interpretation_is_escalated_not_blamed_on_his_words(client, service, monkeypatch):
    """SPEC 2.2. The fault is ours, and it fails the same way on retry, so he
    is not asked to retry. A dispatcher gets his raw text; the trip is untouched."""
    def fail(text):
        raise RuntimeError("provider token must not be exposed")
    monkeypatch.setattr(service, "interpret", fail)
    before = service.state
    message_id = str(uuid4())
    for _ in range(2):
        response = post(client, "Reached the gate.", message_id)
    reply = service.exchanges[-1].reply
    assert reply.mode is ReplyMode.ESCALATE
    assert "The fault is ours, not your words" in reply.text
    assert "A dispatcher has your message" in reply.text
    assert "couldn't read" not in reply.text and "try again" not in reply.text
    assert "Nothing has been recorded on your trip." in response.text
    assert "Reached the gate." in response.text
    assert "provider token" not in response.text
    assert service.state is before
    assert len(service.exchanges) == 1
    assert not service.pending_messages
    html = client.get("/dispatcher").text
    assert "Processing failure" in html and "Reached the gate." in html
    assert "provider token" not in html


def test_a_prose_answer_and_an_illegible_message_never_share_a_reply(client, service, monkeypatch):
    """The regression. The model answered a clean English question in prose and
    the driver was told his message could not be read. Illegible is a statement
    about his words; a failure is a statement about us."""
    def prose(text):
        raise InterpreterError("Not JSON: I cannot answer that question.")
    monkeypatch.setattr(service, "interpret", prose)
    post(client, "How much free waiting time does this customer allow?")
    failed = service.exchanges[-1]
    monkeypatch.setattr(service, "interpret", lambda text: InterpreterOutput(
        intents=[Intent.REPORT], language=Language.EN, transcript_legible=False))
    post(client, "sdkjfh skdjfh")
    illegible = service.exchanges[-1]
    assert failed.failure and not illegible.failure
    assert "fault is ours" in failed.reply.text and "could not make out" not in failed.reply.text
    assert "could not make out" in illegible.reply.text and "fault is ours" not in illegible.reply.text


def test_missing_sop_service_retains_report_and_escalates(client, service, monkeypatch):
    interpret_as(monkeypatch, service, EventType.GATE_CLOSED)
    def fail(*args):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(service, "retrieve", fail)
    response = post(client, "Gate still closed.")
    assert response.status_code == 200
    assert service.state.events[-1].event_type is EventType.GATE_CLOSED
    assert service.exchanges[-1].reply.mode is ReplyMode.ESCALATE
    assert not service.exchanges[-1].reply.claims
    assert "ESCALATE" in client.get("/dispatcher").text


def test_claims_have_exact_source_and_actual_weighted_scores(client, service, monkeypatch):
    interpret_as(monkeypatch, service, EventType.GATE_CLOSED, driver_claimed_wait_minutes=80)
    source = stub_rules(monkeypatch, service)
    response = post(client, "<script>alert('transcript')</script>")
    assert "What we recorded" in response.text
    assert "What the customer’s rules say" in response.text
    assert "SOP-TEST-001" in response.text
    assert "Sixty minutes free." in response.text
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;" in response.text
    reply = service.exchanges[-1]
    assert reply.chunks == (source,)
    assert reply.assessment.signals == dict(entity_resolution=1, transcript_legible=1, retrieval_score=.8, self_rating=.9)
    assert reply.assessment.confidence == .93
    html = client.get("/dispatcher").text
    assert "SPEAK_HEDGED" in html and "HIGH risk" in html and "0.93" in html
    assert "80<small> min" in html
    assert "₹1.50" in html  # exposure still uses 61 observed minutes, not 80 claimed


def test_rejected_claim_is_hidden_from_driver_and_explained_on_board(client, service, monkeypatch):
    interpret_as(monkeypatch, service, EventType.GATE_CLOSED)
    stub_rules(monkeypatch, service, supported=False)
    response = post(client, "Gate closed.")
    assert "You have 60 minutes free" not in response.text
    assert "What the customer’s rules say" not in response.text
    html = client.get("/dispatcher").text
    assert "ESCALATE" in html and "Why the draft was withheld" in html
    assert "The passage does not support this rule" in html


def test_check_exception_updates_both_surfaces(client, service, monkeypatch):
    stub_rules(monkeypatch, service)
    exception = service.state.exceptions[0]
    response = client.post(f"/dispatcher/exceptions/{exception.id}/assess", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "SPEAK_HEDGED" in response.text
    assert "View source" in client.get("/driver").text


def test_approval_is_explicit_idempotent_and_does_not_approve_resolution(client, service):
    draft = next(iter(service.approvals.values()))
    assert draft.status == "PENDING"
    url = f"/dispatcher/approvals/{draft.id}"
    assert client.get(url).status_code == 405
    for _ in range(2):
        assert client.post(url, data={"action": "APPROVED"}).status_code == 200
    assert draft.status == "APPROVED"
    assert "Approved · not sent" in client.get("/dispatcher").text
    assert all(ex.resolution_status.value == "PENDING" for ex in service.state.exceptions)
    assert "already been reviewed" in client.post(url, data={"action": "REJECTED"}).text


def test_closed_exception_cannot_approve_stale_draft(client, service, monkeypatch):
    draft = next(iter(service.approvals.values()))
    interpret_as(monkeypatch, service, EventType.SERVICE_STARTED)
    post(client, "Unloading now.")
    response = client.post(f"/dispatcher/approvals/{draft.id}", data={"action": "APPROVED"})
    assert "no longer current" in response.text
    assert draft.status == "PENDING"


def test_input_validation_and_no_javascript_fallback(client, service, monkeypatch):
    assert "between 1 and 2,000" in post(client, " ").text
    assert "between 1 and 2,000" in post(client, "x" * 2001).text
    assert client.post("/driver/messages", content="x" * 17000,
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
    assert client.post("/driver/messages", json={"text": "x"}).status_code == 415
    interpret_as(monkeypatch, service, EventType.SERVICE_STARTED)
    assert "<!doctype html>" in post(client, "Unloading started.", htmx=False).text


def test_clock_change_during_model_call_cannot_commit_stale_report(service, monkeypatch):
    def during_interpretation(text):
        service.advance()
        return InterpreterOutput(intents=[Intent.REPORT], language=Language.EN,
                                 event_type=EventType.SERVICE_STARTED)
    monkeypatch.setattr(service, "interpret", during_interpretation)
    with pytest.raises(MessageUnavailable, match="shift changed"):
        service.submit("Unloading now.", str(uuid4()))
    assert service.state.stop("stop-2").status.value == "ARRIVED"
    assert not service.exchanges


def test_a_question_with_no_event_reaches_the_rules_instead_of_a_human(client, service, monkeypatch):
    """SPEC 1: a question takes the right-hand branch. It has no event in it,
    and a message with no event is not a message we failed to read.

    This escalated every question a driver could ask. The interpreter had it
    right every time — intents QUESTION, question_text populated, transcript
    legible — and the single word `event_type` in `unresolved_fields` sent it
    to a human as illegible.
    """
    interpret_as(monkeypatch, service, None, intents=[Intent.QUESTION],
                 question_text="how much free waiting time do I have here?",
                 unresolved_fields=["event_type"])
    contexts = capture_context(monkeypatch)
    stub_rules(monkeypatch, service)
    before = service.state.stop("stop-2").status

    post(client, "how much free waiting time do I have here")

    reply = service.exchanges[-1].reply
    assert reply.mode is not ReplyMode.ESCALATE
    assert "could not make out" not in reply.text
    # It reached retrieval, and the responder was told what he actually asked.
    assert contexts and "how much free waiting time" in contexts[-1].render()
    assert reply.cited_sop_ids == ["SOP-TEST-001"]
    # Asking is not progress. Nothing moved, and no UNCLEAR was recorded.
    assert service.state.events[-1].event_type is EventType.ACKNOWLEDGEMENT
    assert service.state.stop("stop-2").status is before


def test_an_illegible_question_is_still_escalated(client, service, monkeypatch):
    """The other half of it. We drop `event_type` because there was no event to
    name — not because nothing can be unreadable any more."""
    interpret_as(monkeypatch, service, None, intents=[Intent.QUESTION],
                 transcript_legible=False, unresolved_fields=["event_type"])
    post(client, "ipp entho cheyth")
    assert service.exchanges[-1].reply.mode is ReplyMode.ESCALATE


def test_an_older_claimed_wait_is_not_read_back_as_a_fresh_one(client, service, monkeypatch):
    """He said eight minutes at 10:20. It is 11:13 and this message has no
    number in it, so "you say 8, we count 61" is not a discrepancy to surface —
    it is 53 minutes of elapsed time reported as a disagreement."""
    assert detention.claimed_wait_minutes(service.state, "stop-2") == 8
    interpret_as(monkeypatch, service, None, intents=[Intent.QUESTION],
                 question_text="when can I leave?")
    contexts = capture_context(monkeypatch)
    stub_rules(monkeypatch, service)

    post(client, "sir enthu cheyyanam")

    rendered = contexts[-1].render()
    assert "he claims" not in rendered
    assert "do not read it back to him" in rendered
    # Still the stop's figure for the board and the ledger, which is where
    # SPEC 4.2 puts it.
    assert waiting_at(service.state, "stop-2", service.now).driver_claimed_wait_minutes == 8
    assert "8<small> min" in client.get("/dispatcher").text


def test_the_reply_speaks_the_seeded_language_not_the_messages(client, service, monkeypatch):
    """His language comes from his record. One romanised transcript read as
    Hindi had a Malayalam speaker answered in three scripts at once."""
    assert service.state.driver_language is Language.ML
    interpret_as(monkeypatch, service, EventType.GATE_CLOSED)   # message read as English
    contexts = capture_context(monkeypatch)
    stub_rules(monkeypatch, service)

    post(client, "gate ippozhum adachirikkuva")

    assert "Write in ml" in contexts[-1].render()
    assert service.exchanges[-1].reply.language is Language.ML


def test_the_office_line_is_said_once_on_the_fallback_path(client, service, monkeypatch):
    """`_fallback` wrote its own "I've flagged this for the office" on top of an
    issue line that already said the office needed to check. It goes through the
    router's wording now, like every other escalation."""
    monkeypatch.setattr(service, "interpret", lambda text: InterpreterOutput(
        intents=[Intent.REPORT], language=Language.EN, transcript_legible=False))
    post(client, "sdkjfh skdjfh")
    text = service.exchanges[-1].reply.text
    assert service.exchanges[-1].reply.mode is ReplyMode.ESCALATE
    assert text.lower().count("office") == 1


def test_replay_critics_are_captured_checks_with_real_weighted_signals(service):
    for minute in (133, 242, 342, 376, 390, 600):
        values = context(service, minute=minute)
        for card in values['attention'] + values['handled']:
            assessment = card['assessment']
            assert assessment is not None and assessment.grounding_ran
            assert assessment.confidence == safety_critic.weighted_confidence(assessment.signals)
            assert card['exception'].confidence == assessment.confidence
            assert card['exchange'].origin == 'captured replay'
            assert card['assessed_at'] <= values['now']
            assert card['weakest_key'] == min(assessment.signals, key=assessment.signals.get)
    assert not context(service, minute=132)['attention']


def test_watchdog_cards_appear_without_driver_input_and_then_resolve(client, service):
    before = service.state
    for before_minute, minute, kind in [(341, 342, EventType.DRIVER_SILENT),
                                      (375, 376, EventType.STOP_OVERDUE)]:
        old = service.replay(before_minute)
        new = service.replay(minute)
        assert old.last_driver_event() == new.last_driver_event()
        assert not any(ex.exception_type is kind for ex in old.exceptions)
        card = next(card for card in context(service, minute=minute)['attention']
                    if card['exception'].exception_type is kind)
        assert card['opening'].source == 'system'
        assert card['opening'].raw_transcript is None
        html = client.get(f'/dispatcher?minute={minute}').text
        assert 'Watchdog · no driver message' in html
        assert 'Reasoning trace' in html and 'Clock rule; no transcript' in html
    after = context(service, minute=390)
    assert not after['attention']
    assert len(after['handled']) == 4
    assert service.state is before


def test_driver_seed_shows_last_message_answer_sources_and_complete_record(client, service):
    html = client.get('/driver').text
    assert service.state.last_driver_event().raw_transcript in html
    assert 'CITED CLAIMS' in html and 'View source' in html
    assert 'FACTS' in html
    for event in service.state.events:
        assert event.ingested_at.strftime('%H:%M') in html
    assert 'You tell us' not in html and 'eyebrow' not in html


def test_one_trace_per_message_tagged_with_what_finds_it(client, service, monkeypatch, langfuse):
    """SPEC 7.1. The whole message as one tree, found by its message id, with
    the router's decision on it — and no user id, because Langfuse would build
    a per-driver view out of one (rule 1)."""
    interpret_as(monkeypatch, service, EventType.GATE_CLOSED)
    stub_rules(monkeypatch, service)
    message_id = str(uuid4())
    post(client, "gate ippozhum adachirikkuva", message_id)

    root = langfuse.named("driver message")
    assert root.parent is None and root.trace_context == {"trace_id": f"trace-{message_id}"}
    assert [node.name for node in langfuse.observations] == [
        "driver message", "identity", "safety critic", "router"]
    assert all(node.parent == "driver message" for node in langfuse.observations[1:])
    tags = set().union(*(attributes["tags"] for attributes in langfuse.attributes))
    assert {"trip:trip-1", f"message:{message_id}", "stop:stop-2"} <= tags
    assert any(tag.startswith("exception:") for tag in tags)
    assert {attributes["session_id"] for attributes in langfuse.attributes} == {"trip-1"}
    assert not any(attributes.get("user_id") for attributes in langfuse.attributes)

    decision, reply = langfuse.named("router").fields["output"], service.exchanges[-1]
    assert decision["mode"] == reply.reply.mode.value
    assert decision["confidence"] == reply.assessment.confidence
    assert set(decision["signals"]) == set(safety_critic.WEIGHTS)
    assert decision["weakest"] == min(decision["signals"], key=decision["signals"].get)
    assert root.trace_io["output"] == reply.reply.text


def test_the_context_gives_the_responder_no_internal_names_to_copy(client, service, monkeypatch):
    """Sonnet spoke "GATE_CLOSED exception … open ആണ്" to a driver, copied from
    the enum values its context handed it. Facts read to a driver never carry
    them, so the context has none to copy."""
    interpret_as(monkeypatch, service, EventType.GATE_CLOSED)
    contexts = capture_context(monkeypatch)
    stub_rules(monkeypatch, service)
    post(client, "gate ippozhum adachirikkuva")
    rendered = contexts[-1].render()
    assert responder.INTERNAL_NAMES.findall(rendered) == []
    assert "The gate is closed" in rendered and "arrived, unloading not started" in rendered
    assert "exception" not in rendered and "status" not in rendered


@pytest.mark.parametrize("broken", ["exploding", "brittle"])
def test_a_broken_langfuse_changes_nothing(monkeypatch, broken_langfuse, broken):
    """SPEC 7.1: traces are best effort, never load bearing. The same message
    with Langfuse off and with Langfuse raising gives the same reply, the same
    record and the same exceptions."""
    def run(backend):
        monkeypatch.setattr(tracing, "_load", lambda: backend)
        shift = ShiftService()
        interpret_as(monkeypatch, shift, EventType.GATE_CLOSED, driver_claimed_wait_minutes=80)
        stub_rules(monkeypatch, shift)
        shift.submit("gate ippozhum adachirikkuva", "7c9e6679-7425-40de-944b-e07fc1f90ae7")
        return shift.exchanges[-1].reply, shift.state.events, shift.state.exceptions

    fake = broken_langfuse[broken]
    assert run(None) == run((fake, fake.propagate))
