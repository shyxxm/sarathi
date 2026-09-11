"""Node 6. Which of the three things happens. SPEC 5.

Pure: assessment in, `DriverReply` out. No model call, no clock, no I/O. The
table below is the whole routing decision and it lives here, in code, because a
prompt that can name its own `ReplyMode` is a prompt deciding when to escalate.

The router is also where a rejected claim stops being spoken. Dropping a claim
from `claims` does not remove the sentence that made it from `text` — the draft
still says the wrong thing. So when grounding rejects anything, the drafted
text is not spoken at all. It is replaced by a reply built from
`restated_facts`, which came from state and were never in question.

**This module is the only place the follow-up line is written.** "Someone at
the office is checking" is a statement about routing, and routing is decided
here — so the responder is told not to write it, the escalation template says
it once rather than twice, and `ShiftService._fallback` comes through
`escalation_text` instead of appending its own. It was being added at three
independent points and the driver heard it three times in one breath.

The language is decided here too, by the caller, from the driver's record. The
draft reports what language it thinks it used; that is the model's opinion of
its own output and it is not what we key the spoken wrapper off.
"""

from app.agents.safety_critic import Assessment
from app.contracts.enums import Language, ReplyMode
from app.contracts.event import InterpreterOutput
from app.contracts.reply import DriverReply, ResponderOutput
from app.domain import words

SPEAK_FLOOR = 0.75
HEDGE_FLOOR = 0.5

# ESCALATE still speaks. It never returns silence, and it never returns a bare
# apology either — the driver gets his facts back plus the honest statement
# that a person has it. Heard, not read: short, no filler, no "unfortunately".
#
# One closing sentence, not two. "I'm checking with the office" followed by
# "someone is looking at it now" is the same sentence twice, and it was landing
# on top of a draft that had already said it.
#
# The wording, in every language, is `escalation.*`, `failure.closing` and
# `hedge` in `domain/words.py` (SPEC 7.2).


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


def escalation_text(facts: list[str], language: Language) -> str:
    """His facts back, then one sentence saying a person has it.

    Public because `ShiftService` escalates too, when the interpreter or the
    reply services are down. It used to word that itself, which is how the
    office line ended up being said twice on the fallback path and three times
    on the hedged one.
    """
    # Facts arrive as sentences with their own full stop, and the template
    # closes with one: "…which stop this is about.." was spoken to a driver.
    joined = "; ".join(fact.rstrip(" .") for fact in facts)
    return (f"{words.say(language, 'escalation.opening', facts=joined)} "
            f"{words.say(language, 'escalation.closing')}")


def failure_text(facts: list[str], language: Language) -> str:
    """SPEC 2.2: the interpreter failed, so there is no reading of his to
    restate — only what happened to his message, and who has it now. Not
    "Recorded:", because nothing was. In the language the facts are in."""
    return f"{' '.join(facts)} {words.say(language, 'failure.closing')}"


def route(
    draft: ResponderOutput,
    assessment: Assessment,
    *,
    understood: InterpreterOutput,
    language: Language,
    facts: tuple[str, ...],
    audio_path: str | None = None,
) -> DriverReply:
    """Assemble what actually gets spoken.

    Only claims that survived grounding reach the reply, and `cited_sop_ids` is
    derived from them, so the contract invariant in SPEC 3.4 holds by
    construction rather than by the caller remembering.

    `language` is the driver's, passed in by the caller from his record. The
    wrapper sentences are keyed off it rather than off `draft.language`, which
    is the model's report of what it thinks it wrote — a draft that came back
    labelled `hi` for a Malayalam speaker got a Hindi closing line stapled to
    romanised Malayalam text, and the driver heard three scripts in one reply.
    """
    mode = mode_for(assessment, understood=understood)
    claims = assessment.grounded_claims

    if mode is ReplyMode.ESCALATE:
        # The draft may contain the very sentence grounding rejected, so none
        # of it is spoken. His facts are: code wrote them from state (SPEC 5.2)
        # in the language `words.spoken` chose, and the wrapper is said in that
        # same one — never his language around English facts (SPEC 7.2).
        language = words.spoken(language)
        text = escalation_text(list(facts), language)
        claims = ()
    elif mode is ReplyMode.SPEAK_HEDGED:
        # Appended to text the responder wrote in his language, so this one
        # line is his language wherever it is written.
        text = f"{draft.text} {words.line(language, 'hedge')}"
    else:
        text = draft.text

    return DriverReply(
        mode=mode,
        language=language,
        text=text,
        restated_facts=list(facts),
        claims=list(claims),
        cited_sop_ids=[claim.cited_chunk_id for claim in claims],
        audio_path=audio_path,
    )
