"""Node 5. How much to trust this reply, and how much it could cost. SPEC 5.

Two parts, and only one of them is a model call.

`signals()` is arithmetic over things code already knows, and the model's own
rating is one capped input among four. The prompt does not get to decide the
routing — that is `router.py`, off these numbers.

`ground()` is the model call, and it is what SPEC 5.1 concluded rule 7 actually
needs. It does not ask whether the reply is good. It asks, of each claim
separately, whether the passage it cites says what it says — because retrieval
similarity cannot tell a chunk that answers the question from a chunk that
shares its vocabulary, and 0.640 of noise outranked 0.635 of real question on
the seeded corpus.
"""

from dataclasses import dataclass
from functools import cache
import os
from pathlib import Path
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import tracing
from app.agents import model_json
from app.contracts.decision import ActionType, Decision
from app.contracts.enums import Intent
from app.contracts.event import InterpreterOutput
from app.contracts.reply import Claim, ResponderOutput
from app.contracts.retrieval import RetrievedChunk

PROMPT_PATH = Path(__file__).parent / "prompts" / "grounding.md"

# SPEC 5: four signals. The model's self-rating is capped at 0.20 — it is the
# only one that can be confidently wrong for the same reason the reply is.
WEIGHTS = {
    "entity_resolution": 0.30,
    "transcript_legible": 0.25,
    "retrieval_score": 0.25,
    "self_rating": 0.20,
}

# Above this, a reply is making a claim with real money behind it. Rs 500.
HIGH_EXPOSURE_PAISE = 50_000


class CriticError(RuntimeError):
    """The grounding check returned something we cannot act on."""


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    supported: bool
    affects_pay_or_liability: bool
    reason: str = ""


class GroundingOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdicts: list[Verdict]
    unclaimed_assertions: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Assessment:
    """What the router routes on."""

    confidence: float
    risk: Literal["LOW", "HIGH"]
    signals: dict[str, float]

    # Claims that survived grounding, in the order the responder made them.
    grounded_claims: tuple[Claim, ...] = ()
    # Claims that did not, with why. Kept for the dispatcher board and the
    # audit trail: a dropped claim is the most interesting thing that can
    # happen to a reply, and it must not vanish silently.
    rejected_claims: tuple[tuple[Claim, str], ...] = ()
    unclaimed_assertions: tuple[str, ...] = ()
    grounding_ran: bool = True

    @property
    def has_ungrounded_pay_claim(self) -> bool:
        """Hard override 1 in SPEC 5, and the reason grounding exists."""
        return bool(self.rejected_claims) or bool(self.unclaimed_assertions)


@cache
def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def strong_model() -> str:
    model = os.getenv("LITELLM_MODEL_STRONG")
    if not model:
        raise CriticError("LITELLM_MODEL_STRONG is not set; see .env.example")
    return model


def signals(
    *,
    understood: InterpreterOutput,
    retrieval_score: float,
    self_rating: float,
    stt_confidence: float | None = None,
) -> dict[str, float]:
    """SPEC 5, computed in code. No model call, no clock, no I/O.

    Typed text uses the interpreter's boolean. Voice callers pass the minimum
    segment confidence, or zero credit when it was not supplied. Missing voice
    confidence is retained as unavailable on the exchange, never invented as
    a measurement. An illegible transcript scores 0.0 either way.
    """
    legible = 0.0 if not understood.transcript_legible else (
        1.0 if stt_confidence is None else max(0.0, min(1.0, stt_confidence))
    )
    return {
        "entity_resolution": 0.0 if understood.unresolved_fields else 1.0,
        "transcript_legible": legible,
        "retrieval_score": max(0.0, min(1.0, retrieval_score)),
        "self_rating": max(0.0, min(1.0, self_rating)),
    }


def weighted_confidence(values: dict[str, float]) -> float:
    missing = set(WEIGHTS) - set(values)
    if missing:
        raise CriticError(f"Missing signals: {sorted(missing)}")
    return round(sum(values[name] * weight for name, weight in WEIGHTS.items()), 4)


def assess_risk(
    *,
    understood: InterpreterOutput,
    pay_claim_present: bool,
    cost_exposure_paise: int,
    decision: Decision | None,
) -> Literal["LOW", "HIGH"]:
    """SPEC 5. Any of four things makes this expensive to get wrong."""
    drafts_customer_message = decision is not None and any(
        action.type is ActionType.DRAFT_CUSTOMER_MESSAGE for action in decision.actions
    )
    if (
        pay_claim_present
        or cost_exposure_paise > HIGH_EXPOSURE_PAISE
        or drafts_customer_message
        or Intent.CORRECTION in understood.intents
    ):
        return "HIGH"
    return "LOW"


def ground(
    draft: ResponderOutput,
    chunks: tuple[RetrievedChunk, ...],
    *,
    records: tuple[str, ...],
    model: str | None = None,
) -> GroundingOutput:
    """Ask whether each claim is actually in the passage it cites.

    SPEC 5.1. Entailment, not similarity — this is where rule 7 is enforced.
    """
    # No early return for a claimless reply. That is the evasion this check
    # exists to close: a draft can state a rule in its prose and simply not
    # declare it, and skipping the call there would check every reply except
    # the one that hid its claim. Costs one call on the same messages the
    # responder already ran on — exceptions and questions, not the whole shift.
    by_id = {chunk.id: chunk for chunk in chunks}
    missing = sorted({claim.cited_chunk_id for claim in draft.claims} - set(by_id))
    if missing:
        raise CriticError(f"Cannot check claims citing chunks not supplied: {missing}")

    rendered, model = _render(draft, by_id, records), model or strong_model()
    with tracing.observe("grounding", as_type="generation", model=model, input=rendered) as call:
        response = _complete(rendered, model)
        content = response.choices[0].message.content or ""
        call.record(lambda: {"output": content, "usage_details": tracing.usage(response)})
        output = _parse(content)
        if len(output.verdicts) != len(draft.claims):
            raise CriticError(
                f"Grounding returned {len(output.verdicts)} verdicts for "
                f"{len(draft.claims)} claims"
            )
    return output


def _render(draft: ResponderOutput, by_id: dict[str, RetrievedChunk], records: tuple[str, ...]) -> str:
    blocks = ["## The reply as drafted", "", draft.text, ""]
    # The checker is asked to ignore what came from our own records, so it has
    # to be told what those are — by code, not by the reply. When this list was
    # the responder's own `restated_facts`, grounding was asked to take a
    # model's word for which figures were ours, and declined (SPEC 5.2).
    blocks += ["## What we already knew (written by our system from its own records, not from any SOP)", ""]
    blocks += [f"- {fact}" for fact in records]
    blocks += ["", "## Claims to check", ""]
    if not draft.claims:
        blocks += ["(the reply declares no claims — check the prose anyway)", ""]
    for index, claim in enumerate(draft.claims, start=1):
        blocks += [
            f"### Claim {index}",
            f"claim: {claim.text}",
            f"cites: [{claim.cited_chunk_id}]",
            "passage:",
            by_id[claim.cited_chunk_id].text.strip(),
            "",
        ]
    return "\n".join(blocks)


def _complete(rendered: str, model: str):
    # litellm costs ~1.8s to import and the deterministic suite must stay under
    # a second (CLAUDE.md). Nothing above this line needs a provider.
    import litellm

    from app.agents.interpreter import ATTEMPTS, TRANSIENT, _pause

    for attempt in range(ATTEMPTS):
        try:
            return litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt()},
                    {"role": "user", "content": rendered},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except TRANSIENT as error:
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(_pause(error, attempt))


def _parse(content: str) -> GroundingOutput:
    # Sonnet puts a note after its fenced verdicts ("**Note on the unclaimed
    # assertion:** …"); the verdicts are the answer. SPEC 3.7.
    try:
        payload = model_json.extract(content)
    except model_json.NoSingleObject as error:
        raise CriticError(f"Grounding check did not return one JSON object ({error}): {content[:200]}") from error
    try:
        return GroundingOutput.model_validate(payload)
    except ValidationError as error:
        raise CriticError(f"Grounding check returned a bad shape: {error}") from error


def assess(**kwargs) -> Assessment:
    """Node 5, observed. `_assess` is the whole of it; this puts what it
    concluded on the message's trace."""
    with tracing.observe("safety critic", as_type="evaluator") as span:
        assessment = _assess(**kwargs)
        span.record(lambda: {"output": {
            "confidence": assessment.confidence, "risk": assessment.risk,
            "signals": assessment.signals, "grounding_ran": assessment.grounding_ran,
            "grounded_claims": len(assessment.grounded_claims),
            "rejected_claims": len(assessment.rejected_claims),
        }})
        return assessment


def _assess(
    *,
    understood: InterpreterOutput,
    draft: ResponderOutput,
    chunks: tuple[RetrievedChunk, ...],
    records: tuple[str, ...],
    retrieval_score: float,
    stt_confidence: float | None = None,
    cost_exposure_paise: int = 0,
    decision: Decision | None = None,
    model: str | None = None,
) -> Assessment:
    """The whole of node 5: four signals, then grounding, then risk.

    A grounding check that fails to run is not a pass. The call raising means
    every claim is dropped and the reply escalates — the same outcome as every
    claim being rejected, because we know exactly as much either way.
    """
    values = signals(
        understood=understood, retrieval_score=retrieval_score,
        self_rating=draft.confidence, stt_confidence=stt_confidence,
    )

    grounding_ran, rejected, grounded, unclaimed = True, [], [], ()
    pay_claim_present = False
    try:
        verdicts = ground(draft, chunks, records=records, model=model).model_dump()
    except CriticError:
        grounding_ran = False
        rejected = [(claim, "grounding check did not complete") for claim in draft.claims]
    else:
        unclaimed = tuple(verdicts["unclaimed_assertions"])
        for claim, verdict in zip(draft.claims, verdicts["verdicts"], strict=True):
            pay_claim_present |= verdict["affects_pay_or_liability"]
            if verdict["supported"]:
                grounded.append(claim)
            else:
                rejected.append((claim, verdict["reason"] or "not supported by the cited passage"))

    return Assessment(
        confidence=weighted_confidence(values),
        risk=assess_risk(
            understood=understood,
            pay_claim_present=pay_claim_present or not grounding_ran,
            cost_exposure_paise=cost_exposure_paise, decision=decision,
        ),
        signals=values,
        grounded_claims=tuple(grounded),
        rejected_claims=tuple(rejected),
        unclaimed_assertions=unclaimed,
        grounding_ran=grounding_ran,
    )
