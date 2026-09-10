"""Node 3. What to say to the driver. SPEC 3.4.

Two halves, and the first one is not a model call. `assemble()` builds what the
responder is allowed to see — resolved stop, resolved customer, the clock as
`domain/` computed it, and only the retrieved chunks that cleared the relevance
floor. `respond()` sends that and validates what comes back.

The model gets no ids it could echo into a citation except the chunk ids in
front of it, and `ResponderOutput` has no `mode` field, so it cannot route
itself. Identity is already resolved before anything here runs (rule 3), and
nothing here writes state (rule 4).
"""

from dataclasses import dataclass
from datetime import datetime
from functools import cache
import json
import os
from pathlib import Path
import textwrap
import time

from pydantic import ValidationError

from app.contracts.enums import EventType, Intent, Language
from app.contracts.event import InterpreterOutput
from app.contracts.exception import OperationalException
from app.contracts.reply import ResponderOutput
from app.contracts.retrieval import RetrievalResult, RetrievedChunk
from app.domain.detention import Detention
from app.domain.state_machine import CustomerTerms, StopState

PROMPT_PATH = Path(__file__).parent / "prompts" / "responder.md"


class ResponderError(RuntimeError):
    """The model returned something we will not speak to a driver."""


@cache
def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def strong_model() -> str:
    """STACK.md: the responder runs on the strong tier, only on exceptions."""
    model = os.getenv("LITELLM_MODEL_STRONG")
    if not model:
        raise ResponderError("LITELLM_MODEL_STRONG is not set; see .env.example")
    return model


def _rupees(paise: int) -> str:
    return f"{paise / 100:.2f}"


def _minutes(count: int) -> str:
    """The context is read back to the driver almost verbatim. "1 minutes" in
    front of the model is "1 minutes" out of the speaker."""
    return f"{count} minute" if count == 1 else f"{count} minutes"


@dataclass(frozen=True)
class ResponderContext:
    """Everything the responder sees, and nothing else.

    Deliberately flat and already resolved. There is no trip state in here to
    search, no customer list to pick from and no raw retrieval result to
    re-rank — the model's job is to say the thing, not to work out which stop
    or which customer or whether a chunk was good enough.
    """

    now: datetime
    transcript: str | None
    language: Language
    intents: tuple[Intent, ...]
    event_type: EventType | None
    question_text: str | None
    driver_claimed_wait_minutes: int | None

    stop: StopState | None
    customer: CustomerTerms | None
    detention: Detention | None
    exception: OperationalException | None

    # Cleared the relevance floor. Chunks that did not are not here at all —
    # the responder must not be able to cite what §5.1 already rejected.
    sop_chunks: tuple[RetrievedChunk, ...]
    precedents: tuple[RetrievedChunk, ...]

    def render(self) -> str:
        """The exact text the model is handed. Kept readable on purpose: this
        is the artefact you inspect when a reply comes out wrong."""
        return "\n".join(filter(None, [
            self._heard(), self._situation(), self._clock(), self._rules(), self._task(),
        ]))

    def _heard(self) -> str:
        lines = ["## What the driver said", ""]
        lines.append(f'transcript: "{self.transcript}"' if self.transcript
                     else "transcript: (none — this is a system-raised check-in)")
        lines.append(f"language: {self.language.value}")
        lines.append(f"understood as: {', '.join(i.value for i in self.intents)}"
                     f" / {self.event_type.value if self.event_type else 'UNCLEAR'}")
        if self.question_text:
            lines.append(f"he asked: \"{self.question_text}\"")
        if self.driver_claimed_wait_minutes is not None:
            lines.append(
                f"he says he has been waiting: {_minutes(self.driver_claimed_wait_minutes)} "
                f"(his account — say it back, never bill from it)")
        return "\n".join(lines)

    def _situation(self) -> str:
        if self.stop is None or self.customer is None:
            return "\n## Where he is\n\n(no stop resolved)"
        lines = [
            "", "## Where he is", "",
            f"stop {self.stop.seq} of the day — {self.customer.name}",
            f"stop status: {self.stop.status.value}",
            f"delivery window: {self.stop.window_open:%H:%M}–{self.stop.window_close:%H:%M}",
        ]
        if self.exception is not None:
            lines.append(
                f"open exception: {self.exception.exception_type.value} "
                f"since {self.exception.opened_at:%H:%M} ({self.exception.status.value})")
        return "\n".join(lines)

    def _clock(self) -> str:
        if self.detention is None:
            return ""
        waiting = self.detention
        lines = [
            "", "## The clock", "",
            # No internal references here: the responder copies this wording
            # into restated_facts almost verbatim, and "SPEC 4.2" reached a
            # driver-facing string the first time this ran.
            f"waiting counted from: {waiting.arrival_observed_at:%H:%M} "
            f"(when we learned he had arrived)",
            f"waited so far: {_minutes(waiting.observed_wait_minutes)}",
            f"free time here: {_minutes(waiting.free_detention_minutes)}",
        ]
        if waiting.crossed:
            lines.append(
                f"past free time by: {_minutes(waiting.billable_minutes)} "
                f"at {waiting.detention_rate_paise_per_min} paise/min "
                f"= Rs {_rupees(waiting.exposure_paise)} so far")
        else:
            remaining = waiting.free_detention_minutes - waiting.observed_wait_minutes
            lines.append(f"still inside free time — {_minutes(remaining)} left before it counts")
        if waiting.driver_claimed_wait_minutes is not None:
            lines.append(
                f"he claims {_minutes(waiting.driver_claimed_wait_minutes)}; "
                f"we counted {_minutes(waiting.observed_wait_minutes)}. "
                f"Say both if they differ.")
        return "\n".join(lines)

    def _rules(self) -> str:
        lines = ["", "## This customer's standing instructions", ""]
        if not self.sop_chunks:
            lines.append(
                "(nothing relevant was retrieved — you may not state what the rules say. "
                "Say you will find out.)")
        for chunk in self.sop_chunks:
            lines += [f"[{chunk.id}]", textwrap.indent(chunk.text.strip(), "  "), ""]
        if self.precedents:
            lines += ["", "## How similar cases were resolved before", "",
                      "(context only — never a source for what the terms say)", ""]
            for chunk in self.precedents:
                lines += [f"[{chunk.id}]", textwrap.indent(chunk.text.strip(), "  "), ""]
        return "\n".join(lines)

    def _task(self) -> str:
        return "\n".join([
            "", "## Now write the reply", "",
            f"It is {self.now:%H:%M}. Reply in {self.language.value}.",
            "Return JSON only, in the shape the system prompt gives.",
        ])


def assemble(
    *,
    now: datetime,
    understood: InterpreterOutput,
    transcript: str | None,
    stop: StopState | None,
    customer: CustomerTerms | None,
    retrieval: RetrievalResult,
    detention: Detention | None = None,
    exception: OperationalException | None = None,
) -> ResponderContext:
    """Build the context. No I/O, no model call — assembled by the caller from
    what code already resolved.

    `retrieval.cited_sop_chunks` rather than `retrieval.sop_chunks`: a chunk
    that did not clear the floor is not shown to the responder at all. Handing
    it over and asking it not to cite it would put §5.1's decision back inside
    a prompt.
    """
    return ResponderContext(
        now=now,
        transcript=transcript,
        language=understood.language,
        intents=tuple(understood.intents),
        event_type=understood.event_type,
        question_text=understood.question_text,
        driver_claimed_wait_minutes=understood.driver_claimed_wait_minutes,
        stop=stop,
        customer=customer,
        detention=detention,
        exception=exception,
        sop_chunks=retrieval.cited_sop_chunks,
        precedents=tuple(
            chunk for chunk in retrieval.precedents if chunk.score >= retrieval.relevance_floor
        ),
    )


def respond(context: ResponderContext, *, model: str | None = None) -> ResponderOutput:
    """Draft the reply. Raises rather than returning something unspeakable."""
    response = _complete(context.render(), model or strong_model())
    return _parse(response.choices[0].message.content or "", context)


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


def _parse(content: str, context: ResponderContext) -> ResponderOutput:
    body = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise ResponderError(f"Not JSON: {content[:200]}") from error

    try:
        output = ResponderOutput.model_validate(payload)
    except ValidationError as error:
        raise ResponderError(f"Bad shape: {error}") from error

    # A citation the model invented, or one for a chunk it was never shown.
    # Caught here rather than at the critic: a fabricated id would otherwise be
    # sent to the grounding check, which would have no text to check it against.
    available = {chunk.id for chunk in context.sop_chunks}
    invented = sorted({claim.cited_chunk_id for claim in output.claims} - available)
    if invented:
        raise ResponderError(f"Claims cite chunks that were not retrieved: {invented}")
    return output
