from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    LOG_DETENTION = "LOG_DETENTION"
    NOTIFY_DISPATCHER = "NOTIFY_DISPATCHER"
    SCHEDULE_REATTEMPT = "SCHEDULE_REATTEMPT"
    SET_REVIEW_TIMER = "SET_REVIEW_TIMER"
    DRAFT_CUSTOMER_MESSAGE = "DRAFT_CUSTOMER_MESSAGE"
    NO_ACTION = "NO_ACTION"


class DecisionAction(BaseModel):
    type: ActionType
    payload: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    """SPEC 3.5."""

    actions: list[DecisionAction]
    rationale: str
    review_in_minutes: int | None = None
    cited_sop_ids: list[str] = Field(default_factory=list)
