from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.contracts.decision import Decision
from app.contracts.enums import EventType, ExceptionStatus, ReplyMode, ResolutionStatus


class OperationalException(BaseModel):
    """SPEC 3.6. Attaches to a trip and a stop, never to a person.
    See CLAUDE.md rule 1."""

    id: str
    trip_id: str
    stop_id: str | None
    exception_type: EventType

    opened_at: datetime
    opened_by: Literal["driver", "system"]
    opening_event_id: str

    status: ExceptionStatus
    resolution_status: ResolutionStatus = ResolutionStatus.PENDING

    # set by the safety critic / router, null until decided
    confidence: float | None = None
    risk: Literal["LOW", "HIGH"] | None = None
    reply_mode: ReplyMode | None = None

    cost_exposure_paise: int = 0
    review_due_at: datetime | None = None
    driver_informed: bool = False

    resolved_at: datetime | None = None
    resolving_event_id: str | None = None
    resolution_note: str | None = None

    decision: Decision | None = None
    audit: list[dict[str, Any]] = Field(default_factory=list)
