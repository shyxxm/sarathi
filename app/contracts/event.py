from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import EventType, Intent, Language


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
