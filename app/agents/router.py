"""Node 6. Which of the three things happens. SPEC 5.

Pure: assessment in, `DriverReply` out. No model call, no clock, no I/O. The
table below is the whole routing decision and it lives here, in code, because a
prompt that can name its own `ReplyMode` is a prompt deciding when to escalate.

The router is also where a rejected claim stops being spoken. Dropping a claim
from `claims` does not remove the sentence that made it from `text` — the draft
still says the wrong thing. So when grounding rejects anything, the drafted
text is not spoken at all. It is replaced by a reply built from
`restated_facts`, which came from state and were never in question.
"""

from app.agents.safety_critic import Assessment
from app.contracts.enums import Language, ReplyMode
from app.contracts.event import InterpreterOutput
from app.contracts.reply import DriverReply, ResponderOutput

SPEAK_FLOOR = 0.75
HEDGE_FLOOR = 0.5

# ESCALATE still speaks. It never returns silence, and it never returns a bare
# apology either — the driver gets his facts back plus the honest statement
# that a person has it. Heard, not read: short, no filler, no "unfortunately".
ESCALATION: dict[Language, tuple[str, str]] = {
    Language.EN: (
        "Recorded: {facts}.",
        "I'm checking the rest with the office. Someone is looking at it now.",
    ),
    Language.ML: (
        "രേഖപ്പെടുത്തി: {facts}.",
        "ബാക്കി ഓഫീസിൽ ചോദിക്കുന്നു. ഒരാൾ ഇത് നോക്കുന്നുണ്ട്.",
    ),
    Language.MIXED: (
        "രേഖപ്പെടുത്തി: {facts}.",
        "ബാക്കി ഓഫീസിൽ ചോദിക്കുന്നു — someone is looking at it now.",
    ),
    Language.HI: (
        "दर्ज कर लिया: {facts}.",
        "बाकी ऑफिस से पूछ रहा हूँ. एक व्यक्ति इसे देख रहा है.",
    ),
}

HEDGE: dict[Language, str] = {
    Language.EN: "I'm confirming this with the office.",
    Language.ML: "ഇത് ഓഫീസിൽ ഉറപ്പാക്കുന്നുണ്ട്.",
    Language.MIXED: "ഇത് ഓഫീസിൽ confirm ചെയ്യുന്നുണ്ട്.",
    Language.HI: "यह ऑफिस से पक्का कर रहा हूँ.",
}


def mode_for(assessment: Assessment, *, understood: InterpreterOutput) -> ReplyMode:
    """SPEC 5's table, plus hard override 1.

    | confidence >= 0.75, risk LOW, nothing ungrounded | SPEAK         |
    | 0.5 <= confidence < 0.75, or risk HIGH           | SPEAK_HEDGED  |
    | confidence < 0.5, or entity unresolved           | ESCALATE      |

    **"No SOP cited" is read as "a claim was made and not supported".** A reply
    that asserts nothing about the customer's rules needs no citation — *"gate
    closed at stop 2, waiting counted from 10:12"* is a restatement of our own
    records, and requiring a SOP behind it would escalate every acknowledgement
    on the shift. The citation requirement attaches to claims, which is what
    rule 7 says: *no claim about the driver's pay or liability without a cited
    SOP*. No claim, nothing to cite.
    """
    if understood.unresolved_fields:
        return ReplyMode.ESCALATE
    # Hard override 1: a claim that failed grounding, or one the reply made
    # without declaring, means the drafted text cannot be trusted as written.
    if assessment.has_ungrounded_pay_claim:
        return ReplyMode.ESCALATE
    if assessment.confidence < HEDGE_FLOOR:
        return ReplyMode.ESCALATE
    if assessment.confidence >= SPEAK_FLOOR and assessment.risk == "LOW":
        return ReplyMode.SPEAK
    return ReplyMode.SPEAK_HEDGED


def _escalation_text(facts: list[str], language: Language) -> str:
    opening, closing = ESCALATION.get(language, ESCALATION[Language.EN])
    return f"{opening.format(facts='; '.join(facts))} {closing}"


def route(
    draft: ResponderOutput,
    assessment: Assessment,
    *,
    understood: InterpreterOutput,
    audio_path: str | None = None,
) -> DriverReply:
    """Assemble what actually gets spoken.

    Only claims that survived grounding reach the reply, and `cited_sop_ids` is
    derived from them, so the contract invariant in SPEC 3.4 holds by
    construction rather than by the caller remembering.
    """
    mode = mode_for(assessment, understood=understood)
    claims = assessment.grounded_claims

    if mode is ReplyMode.ESCALATE:
        # The draft may contain the very sentence grounding rejected, so none
        # of it is spoken. Facts survive: they never came from retrieval.
        text = _escalation_text(draft.restated_facts, draft.language)
        claims = ()
    elif mode is ReplyMode.SPEAK_HEDGED:
        text = f"{draft.text} {HEDGE.get(draft.language, HEDGE[Language.EN])}"
    else:
        text = draft.text

    return DriverReply(
        mode=mode,
        language=draft.language,
        text=text,
        restated_facts=draft.restated_facts,
        claims=list(claims),
        cited_sop_ids=[claim.cited_chunk_id for claim in claims],
        audio_path=audio_path,
    )
