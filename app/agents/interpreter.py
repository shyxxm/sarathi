"""Node 1. Transcript in, meaning out. SPEC 3.2.

The model never sees trip state and has nowhere to put an identity if it
guessed one: `InterpreterOutput` forbids unknown keys, so a model that tries to
return a `stop_id` fails validation instead of being believed. CLAUDE.md rule 3.
"""

import os
import random
import time
from functools import cache
from pathlib import Path

import litellm
from dotenv import load_dotenv
from pydantic import ValidationError

from app import tracing
from app.agents import model_json
from app.contracts.enums import EventType, Intent
from app.contracts.event import InterpreterOutput

load_dotenv()
litellm.suppress_debug_info = True

PROMPT_PATH = Path(__file__).parent / "prompts" / "interpreter.md"

# SPEC 3.1 marks these system-generated. A driver cannot report them, so a
# model that returns one has misread the message.
SYSTEM_ONLY = frozenset({
    EventType.STOP_OVERDUE,
    EventType.DRIVER_SILENT,
    EventType.WINDOW_AT_RISK,
    EventType.DETENTION_CROSSED,
    EventType.REATTEMPT_SCHEDULED,
})


class InterpreterError(RuntimeError):
    """The model returned something we will not build an event out of."""


# Providers 503 under load and rate-limit without warning. A dropped message is
# a report the driver believes he has made, so these are retried in-process
# rather than surfaced. Hand-rolled: litellm's own num_retries wants tenacity,
# and the stack is closed (CLAUDE.md).
TRANSIENT = (
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.Timeout,
)
ATTEMPTS = 4


@cache
def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def cheap_model() -> str:
    """STACK.md: interpretation runs on the cheap tier, ~35 calls a shift."""
    model = os.getenv("LITELLM_MODEL_CHEAP")
    if not model:
        raise InterpreterError("LITELLM_MODEL_CHEAP is not set; see .env.example")
    return model


def interpret(transcript: str, *, model: str | None = None) -> InterpreterOutput:
    """What one message means. Raises rather than returning a guess."""
    if not transcript or not transcript.strip():
        return InterpreterOutput(
            intents=[Intent.CHITCHAT], language="en", event_type=EventType.UNCLEAR,
            transcript_legible=False, unresolved_fields=["event_type"],
        )

    model = model or cheap_model()
    with tracing.observe("interpreter", as_type="generation", model=model, input=transcript) as call:
        response = _complete(transcript, model)
        content = response.choices[0].message.content or ""
        call.record(lambda: {"output": content, "usage_details": tracing.usage(response)})
        return _parse(content, transcript)


def _complete(transcript: str, model: str):
    for attempt in range(ATTEMPTS):
        try:
            return litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt()},
                    {"role": "user", "content": transcript},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except TRANSIENT as error:
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(_pause(error, attempt))


def _parse(content: str, transcript: str) -> InterpreterOutput:
    try:
        payload = model_json.extract(content)
    except model_json.NoSingleObject as error:
        raise InterpreterError(f"Not JSON for {transcript!r} ({error}): {content[:200]}") from error

    try:
        output = InterpreterOutput.model_validate(payload)
    except ValidationError as error:
        raise InterpreterError(f"Bad shape for {transcript!r}: {error}") from error

    if output.event_type in SYSTEM_ONLY:
        raise InterpreterError(
            f"{output.event_type.value} is system-generated; a driver cannot report it")
    return output


def _pause(error: Exception, attempt: int) -> float:
    """How long to wait before trying again, by what went wrong.

    Capacity and connection failures clear in seconds — 1s, 2s, 4s, jittered so
    that callers retrying together do not come back in lockstep. A rate limit is
    the only one worth waiting minutes for, because the window it is counting
    against has to expire first: 4s, 16s, 60s.

    Treating an overload blip like a quota exhaustion cost 80 seconds a message.
    """
    if isinstance(error, litellm.exceptions.RateLimitError):
        return min(60.0, 4.0 ** (attempt + 1))
    return 2.0**attempt * random.uniform(0.8, 1.3)
