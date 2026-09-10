"""SPEC 4.4. What closes what, and what happens to whatever is still open when
the shift ends.

Closing sets `status` to RESOLVED. It does not touch `resolution_status`, which
stays PENDING until a human approves it — that field is what gates precedent
write-back. See CLAUDE.md rule 9.
"""

from collections.abc import Mapping
from datetime import datetime

from app.contracts.enums import EventType, ExceptionStatus, ResolutionStatus
from app.contracts.event import OperationalEvent
from app.contracts.exception import OperationalException
from app.domain.state_machine import StopState, TripState

# SPEC 4.4, exactly. DRIVER_SILENT is deliberately empty: any driver-sourced
# event clears it, whatever it says.
CLOSURES: Mapping[EventType, frozenset[EventType]] = {
    EventType.GATE_CLOSED: frozenset({EventType.SERVICE_STARTED, EventType.STOP_COMPLETED}),
    EventType.CONSIGNEE_ABSENT: frozenset(
        {EventType.STOP_COMPLETED, EventType.REATTEMPT_SCHEDULED}),
    EventType.STOP_OVERDUE: frozenset({EventType.ARRIVED_STOP}),
    EventType.DRIVER_SILENT: frozenset(),
    EventType.WINDOW_AT_RISK: frozenset({EventType.STOP_COMPLETED}),
    EventType.VEHICLE_BREAKDOWN: frozenset({EventType.DEPARTED}),
    EventType.DETENTION_CROSSED: frozenset(
        {EventType.SERVICE_STARTED, EventType.DEPARTED}),
}

LIVE = frozenset({ExceptionStatus.OPEN, ExceptionStatus.ACTING, ExceptionStatus.MONITORING})


def is_open(exception: OperationalException) -> bool:
    return exception.status in LIVE


def closes(
    exception_type: EventType,
    event: OperationalEvent,
    *,
    stop_id: str | None,
    stop: StopState | None = None,
) -> bool:
    """Does this event clear that condition at that stop?

    Also used by the watchdog to decide when a fired rule may fire again: a
    condition that has been cleared is a condition that can recur.
    """
    if exception_type is EventType.DRIVER_SILENT:
        return event.source == "driver"

    resolving = CLOSURES.get(exception_type)
    if not resolving or event.event_type not in resolving:
        return False
    if stop_id is not None and event.stop_id != stop_id:
        return False
    if exception_type is EventType.WINDOW_AT_RISK:
        # "STOP_COMPLETED before close" — a completion after the window closed
        # is not a resolution of the risk, it is the risk having landed.
        return stop is not None and event.occurred_at <= stop.window_close
    return True


def close(
    exception: OperationalException, event: OperationalEvent, now: datetime,
) -> OperationalException:
    """A resolved copy. The original is never touched — exceptions held in
    TripState are shared, and domain/ mutates nothing it was handed."""
    return exception.model_copy(update={
        "status": ExceptionStatus.RESOLVED,
        "resolution_status": ResolutionStatus.PENDING,  # a human approves; rule 9
        "resolved_at": now,
        "resolving_event_id": event.id,
        "resolution_note": f"Closed by {event.event_type.value} from the {event.source}.",
        "audit": [*exception.audit, {
            "at": now.isoformat(), "action": "RESOLVED", "event_id": event.id,
        }],
    })


def close_matching(
    state: TripState, event: OperationalEvent, now: datetime,
) -> list[OperationalException]:
    """Every open exception this event closes, as resolved copies."""
    closed = []
    for exception in state.exceptions:
        if not is_open(exception):
            continue
        stop = state.stop(exception.stop_id)
        if closes(exception.exception_type, event, stop_id=exception.stop_id, stop=stop):
            closed.append(close(exception, event, now))
    return closed


def expire_open(state: TripState, now: datetime) -> list[OperationalException]:
    """SPEC 4.4: still open at trip close -> EXPIRED. Nothing expires early."""
    if now < state.shift_end:
        return []
    return [
        exception.model_copy(update={
            "status": ExceptionStatus.EXPIRED,
            "resolved_at": now,
            "resolution_note": "Still open when the trip closed.",
            "audit": [*exception.audit, {"at": now.isoformat(), "action": "EXPIRED"}],
        })
        for exception in state.exceptions if is_open(exception)
    ]
