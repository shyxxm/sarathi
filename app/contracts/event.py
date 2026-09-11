from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.enums import EventType, Intent, Language

EVENT_TYPE = "event_type"


class InterpreterOutput(BaseModel):
    """SPEC 3.2. Semantics and intent only.

    `extra="forbid"` and `frozen=True` are what make this structurally
    incapable of carrying driver_id, vehicle_id, trip_id or stop_id: those
    keys are rejected at construction and cannot be set afterwards.
    Identity is resolved by code. See CLAUDE.md rule 3.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intents: list[Intent]
    language: Language
    event_type: EventType | None = None
    question_text: str | None = None
    location_hint: str | None = None
    driver_claimed_wait_minutes: int | None = None
    contradicts_recent_state: bool = False
    transcript_legible: bool = True
    unresolved_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _an_unnamed_event_is_only_unresolved_if_he_was_reporting(cls, data):
        """A message that reports nothing has no event to name, so a null
        `event_type` there is the answer and not a failure to find one.

        Enforced on the contract because two separate places read this field
        and both of them escalate on it — the clarification branch in
        `ShiftService.submit`, and `entity_resolution` in the safety critic.
        A driver asking *how much free waiting time do I have here* was told
        his message needed clarification and routed to a human, with the
        question correctly understood the whole way. SPEC 1 sends a question
        past the state machine, not past the pipeline.

        Only when the words arrived whole: a transcript we could not read is
        still unreadable, and `transcript_legible` escalates it on its own.
        """
        if not isinstance(data, dict):
            return data
        unresolved = data.get("unresolved_fields")
        intents = data.get("intents") or []
        # Wrong types are left for field validation to refuse. Iterating them
        # here turned `"event_type"` into ten one-letter unresolved fields, and
        # `"intents": 1` into a TypeError no caller reads as a bad answer.
        if not isinstance(unresolved, list) or not isinstance(intents, list):
            return data
        if not unresolved or EVENT_TYPE not in unresolved:
            return data
        if Intent.REPORT in [Intent(intent) for intent in intents]:
            return data
        if not data.get("transcript_legible", True):
            return data
        return data | {"unresolved_fields": [f for f in unresolved if f != EVENT_TYPE]}


class OperationalEvent(BaseModel):
    """SPEC 3.3."""

    model_config = ConfigDict(frozen=True)

    id: str
    source: Literal["driver", "system"]
    occurred_at: datetime
    ingested_at: datetime
    event_type: EventType

    # from the model
    raw_transcript: str | None = None
    audio_path: str | None = None
    language: Language | None = None
    location_hint: str | None = None
    driver_claimed_wait_minutes: int | None = None

    # from code. NEVER model-populated.
    driver_id: str | None = None
    vehicle_id: str | None = None
    trip_id: str | None = None
    stop_id: str | None = None

    unresolved_fields: list[str] = Field(default_factory=list)
    supersedes_event_id: str | None = None
    source_message_id: str | None = None
