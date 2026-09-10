"""Node 5 and node 6, with the model call stubbed. No network, no clock."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents import router, safety_critic
from app.agents.responder import ResponderContext, ResponderError, _parse as parse_reply
from app.agents.safety_critic import Assessment, CriticError, GroundingOutput, Verdict
from app.contracts.decision import ActionType, Decision, DecisionAction
from app.contracts.enums import EventType, Intent, Language, ReplyMode
from app.contracts.event import InterpreterOutput
from app.contracts.reply import Claim, DriverReply, ResponderOutput
from app.contracts.retrieval import RetrievedChunk, chunk_id

CHUNK_ID = chunk_id("customer-2", "standing-instructions.md", 2)
UNDERSTOOD = InterpreterOutput(
    intents=[Intent.REPORT, Intent.QUESTION], language=Language.EN,
    event_type=EventType.GATE_CLOSED,
)


def chunk(text="Free waiting time: 60 minutes from arrival.", score=0.69):
    return RetrievedChunk(
        id=CHUNK_ID, customer_id="customer-2",
        source_document="standing-instructions.md", text=text, score=score,
    )


def draft(claims=(), confidence=0.9, text="Gate closed at stop 2, noted."):
    return ResponderOutput(
        language=Language.EN, text=text,
        restated_facts=["Gate closed at stop 2", "Waiting counted from 10:12"],
        claims=list(claims), confidence=confidence,
    )


CLAIM = Claim(text="Free waiting time here is 60 minutes.", cited_chunk_id=CHUNK_ID)


def stub_grounding(monkeypatch, *verdicts, unclaimed=()):
    monkeypatch.setattr(
        safety_critic, "ground",
        lambda drafted, chunks, model=None: GroundingOutput(
            verdicts=[Verdict(supported=s, affects_pay_or_liability=p, reason=r)
                      for s, p, r in verdicts],
            unclaimed_assertions=list(unclaimed),
        ),
    )


# --- signals -----------------------------------------------------------------

def test_self_rating_cannot_carry_a_reply_on_its_own():
    """SPEC 5 caps it at 0.20, so a maximally self-assured model with nothing
    else behind it lands under the ESCALATE floor."""
    values = safety_critic.signals(
        understood=InterpreterOutput(
            intents=[Intent.REPORT], language=Language.EN,
            transcript_legible=False, unresolved_fields=["stop_id"],
        ),
        retrieval_score=0.0, self_rating=1.0,
    )
    assert safety_critic.weighted_confidence(values) == pytest.approx(0.20)
    assert safety_critic.weighted_confidence(values) < router.HEDGE_FLOOR


def test_unresolved_entity_and_illegible_transcript_zero_their_signals():
    values = safety_critic.signals(
        understood=InterpreterOutput(
            intents=[Intent.REPORT], language=Language.EN,
            transcript_legible=False, unresolved_fields=["stop_id"],
        ),
        retrieval_score=0.7, self_rating=0.8,
    )
    assert values["entity_resolution"] == 0.0
    assert values["transcript_legible"] == 0.0


def test_weights_sum_to_one_and_missing_signals_fail_closed():
    assert sum(safety_critic.WEIGHTS.values()) == pytest.approx(1.0)
    with pytest.raises(CriticError, match="Missing signals"):
        safety_critic.weighted_confidence({"self_rating": 1.0})


# --- risk --------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, expected", [
    ({}, "LOW"),
    ({"pay_claim_present": True}, "HIGH"),
    ({"cost_exposure_paise": safety_critic.HIGH_EXPOSURE_PAISE + 1}, "HIGH"),
    ({"decision": Decision(
        actions=[DecisionAction(type=ActionType.DRAFT_CUSTOMER_MESSAGE)], rationale="x")}, "HIGH"),
])
def test_risk_triggers(kwargs, expected):
    assert safety_critic.assess_risk(**{
        "understood": UNDERSTOOD, "pay_claim_present": False,
        "cost_exposure_paise": 0, "decision": None, **kwargs,
    }) == expected


def test_a_correction_is_always_high_risk():
    """SPEC 5. Correcting a record we already acted on is where a mistake
    compounds instead of just being wrong."""
    correction = InterpreterOutput(intents=[Intent.CORRECTION], language=Language.EN)
    assert safety_critic.assess_risk(
        understood=correction, pay_claim_present=False,
        cost_exposure_paise=0, decision=None,
    ) == "HIGH"


# --- grounding ---------------------------------------------------------------

def test_supported_claim_survives_and_is_cited(monkeypatch):
    stub_grounding(monkeypatch, (True, True, "passage states 60 minutes"))
    assessment = safety_critic.assess(
        understood=UNDERSTOOD, draft=draft([CLAIM]), chunks=(chunk(),),
        retrieval_score=0.69,
    )
    assert assessment.grounded_claims == (CLAIM,)
    assert assessment.rejected_claims == ()
    assert assessment.risk == "HIGH"  # it is a pay claim
    assert not assessment.has_ungrounded_pay_claim


def test_unsupported_claim_is_dropped_and_forces_escalation(monkeypatch):
    """The diesel case from SPEC 5.1: the chunk came back and scored well, and
    says nothing about what was claimed."""
    stub_grounding(monkeypatch, (False, True, "passage is about the delivery window"))
    assessment = safety_critic.assess(
        understood=UNDERSTOOD, draft=draft([CLAIM]), chunks=(chunk(score=0.64),),
        retrieval_score=0.64,
    )
    assert assessment.grounded_claims == ()
    assert assessment.rejected_claims[0][0] == CLAIM
    assert assessment.has_ungrounded_pay_claim
    assert router.mode_for(assessment, understood=UNDERSTOOD) is ReplyMode.ESCALATE


def test_a_rule_stated_only_in_the_prose_is_caught(monkeypatch):
    """The leak the claims list cannot close by itself: the reply asserts a
    rule and does not declare it as a claim."""
    stub_grounding(monkeypatch, unclaimed=["Your waiting time is being counted."])
    assessment = safety_critic.assess(
        understood=UNDERSTOOD, draft=draft(), chunks=(chunk(),), retrieval_score=0.69,
    )
    assert assessment.unclaimed_assertions
    assert router.mode_for(assessment, understood=UNDERSTOOD) is ReplyMode.ESCALATE


def test_a_grounding_check_that_did_not_run_is_not_a_pass(monkeypatch):
    def explode(*args, **kwargs):
        raise CriticError("provider is down")

    monkeypatch.setattr(safety_critic, "ground", explode)
    assessment = safety_critic.assess(
        understood=UNDERSTOOD, draft=draft([CLAIM]), chunks=(chunk(),), retrieval_score=0.69,
    )
    assert assessment.grounding_ran is False
    assert assessment.grounded_claims == ()
    assert assessment.risk == "HIGH"
    assert router.mode_for(assessment, understood=UNDERSTOOD) is ReplyMode.ESCALATE


def test_grounding_refuses_to_check_a_claim_whose_chunk_was_not_supplied():
    with pytest.raises(CriticError, match="not supplied"):
        safety_critic.ground(draft([CLAIM]), chunks=())


def test_a_claimless_reply_is_still_checked(monkeypatch):
    """The evasion: state the rule in the prose, declare no claims. Skipping
    the call here would check every reply except the one that hid its claim."""
    called = []

    def capture(rendered, model):
        called.append(rendered)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"verdicts": [], "unclaimed_assertions": '
                    '["Free time here is 60 minutes."]}'))])

    monkeypatch.setattr(safety_critic, "_complete", capture)
    output = safety_critic.ground(draft(), chunks=(chunk(),), model="fixture/model")
    assert called, "a claimless reply must still reach the grounding check"
    assert "declares no claims" in called[0]
    assert output.unclaimed_assertions


# --- router ------------------------------------------------------------------

def assessment(confidence, risk="LOW", **kwargs):
    return Assessment(confidence=confidence, risk=risk, signals={}, **kwargs)


@pytest.mark.parametrize("confidence, risk, expected", [
    (0.90, "LOW", ReplyMode.SPEAK),
    (0.75, "LOW", ReplyMode.SPEAK),
    (0.90, "HIGH", ReplyMode.SPEAK_HEDGED),
    (0.60, "LOW", ReplyMode.SPEAK_HEDGED),
    (0.49, "LOW", ReplyMode.ESCALATE),
])
def test_router_table(confidence, risk, expected):
    assert router.mode_for(assessment(confidence, risk), understood=UNDERSTOOD) is expected


def test_unresolved_entity_escalates_however_confident():
    unresolved = InterpreterOutput(
        intents=[Intent.REPORT], language=Language.EN, unresolved_fields=["stop_id"],
    )
    assert router.mode_for(assessment(1.0), understood=unresolved) is ReplyMode.ESCALATE


def test_an_acknowledgement_with_no_claims_still_speaks():
    """SPEC 5: `no SOP cited` attaches to claims. A reply that asserts nothing
    about the rules needs no citation, or every arrival on the shift escalates."""
    reply = router.route(draft(), assessment(0.9), understood=UNDERSTOOD)
    assert reply.mode is ReplyMode.SPEAK
    assert reply.cited_sop_ids == []
    assert reply.text == "Gate closed at stop 2, noted."


def test_escalation_does_not_speak_the_drafted_text():
    """Dropping a claim from the list does not remove the sentence that made
    it. The draft below still says 60 minutes; the reply must not."""
    rejected = assessment(
        0.9, rejected_claims=((CLAIM, "not in the passage"),),
    )
    reply = router.route(
        draft([CLAIM], text="Gate closed. Your free waiting time here is 60 minutes."),
        rejected, understood=UNDERSTOOD,
    )
    assert reply.mode is ReplyMode.ESCALATE
    assert "60 minutes" not in reply.text
    assert reply.claims == [] and reply.cited_sop_ids == []
    # It still speaks, and it still hands the facts back. SPEC 5, §2.
    assert "Gate closed at stop 2" in reply.text
    assert "someone is looking at it now" in reply.text.lower()


def test_escalation_speaks_the_drivers_language():
    malayalam = draft()
    reply = router.route(
        ResponderOutput(**(malayalam.model_dump() | {"language": Language.ML})),
        assessment(0.2), understood=UNDERSTOOD,
    )
    assert reply.mode is ReplyMode.ESCALATE
    assert "ഓഫീസ" in reply.text


def test_hedged_reply_keeps_the_draft_and_says_it_is_confirming():
    reply = router.route(draft([CLAIM]), assessment(0.6, grounded_claims=(CLAIM,)),
                         understood=UNDERSTOOD)
    assert reply.mode is ReplyMode.SPEAK_HEDGED
    assert reply.text.startswith("Gate closed at stop 2, noted.")
    assert "office" in reply.text
    assert reply.cited_sop_ids == [CHUNK_ID]


def test_every_reply_restates_and_citations_track_claims():
    reply = router.route(draft([CLAIM]), assessment(0.9, grounded_claims=(CLAIM,)),
                         understood=UNDERSTOOD)
    assert reply.restated_facts
    assert reply.cited_sop_ids == [claim.cited_chunk_id for claim in reply.claims]
    with pytest.raises(ValidationError, match="does not match the chunks"):
        DriverReply.model_validate(reply.model_dump() | {"cited_sop_ids": ["SOP-OTHER"]})


# --- responder boundary ------------------------------------------------------

def test_responder_cannot_cite_a_chunk_it_was_not_shown():
    context = ResponderContext(
        now=None, transcript="x", language=Language.EN, intents=(Intent.REPORT,),
        event_type=EventType.GATE_CLOSED, question_text=None,
        driver_claimed_wait_minutes=None, stop=None, customer=None, detention=None,
        exception=None, sop_chunks=(chunk(),), precedents=(),
    )
    payload = (
        '{"language": "en", "text": "ok", "restated_facts": ["a"], "confidence": 0.9,'
        ' "claims": [{"text": "60 minutes", "cited_chunk_id": "SOP-INVENTED-001"}]}'
    )
    with pytest.raises(ResponderError, match="not retrieved"):
        parse_reply(payload, context)


def test_responder_cannot_route_itself():
    context = ResponderContext(
        now=None, transcript="x", language=Language.EN, intents=(Intent.REPORT,),
        event_type=None, question_text=None, driver_claimed_wait_minutes=None,
        stop=None, customer=None, detention=None, exception=None,
        sop_chunks=(), precedents=(),
    )
    payload = ('{"language": "en", "text": "ok", "restated_facts": ["a"], '
               '"confidence": 0.9, "mode": "SPEAK"}')
    with pytest.raises(ResponderError, match="Bad shape"):
        parse_reply(payload, context)
