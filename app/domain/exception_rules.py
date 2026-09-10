"""Which events open an exception, and what it costs while it is open.

An exception belongs to a trip and a stop. It never belongs to a person, it
carries no history beyond this trip, and nothing here counts anything about the
driver. See CLAUDE.md rule 1 and SPEC 3.6.
"""

from datetime import datetime

from app.contracts.enums import EventType, ExceptionStatus
from app.contracts.event import OperationalEvent
from app.contracts.exception import OperationalException
from app.domain import detention, resolution
from app.domain.state_machine import TripState

# Problems the driver reports, and the four the watchdog notices. Progress
# events and non-events open nothing.
OPENS = frozenset({
    EventType.GATE_CLOSED,
    EventType.CONSIGNEE_ABSENT,
    EventType.VEHICLE_BREAKDOWN,
    EventType.DOCUMENT_ISSUE,
    EventType.SHORTAGE_OR_DAMAGE,
    EventType.DELIVERY_REFUSED,
    EventType.STOP_OVERDUE,
    EventType.DRIVER_SILENT,
    EventType.WINDOW_AT_RISK,
    EventType.DETENTION_CROSSED,
})


def evaluate(
    state: TripState, event: OperationalEvent, now: datetime,
) -> list[OperationalException]:
    """Exceptions this event opens. Empty if it opens none, and empty if the
    same thing is already open at the same stop — one gate closure at stop 2 is
    one exception, however many times it is mentioned."""
    if event.event_type not in OPENS or _already_open(state, event):
        return []

    return [OperationalException(
        id=f"exception-{event.id}",
        trip_id=state.trip_id,
        stop_id=event.stop_id,
        exception_type=event.event_type,
        opened_at=now,
        opened_by=event.source,
        opening_event_id=event.id,
        status=ExceptionStatus.OPEN,
        cost_exposure_paise=detention.exposure_paise(state, event.stop_id, now),
        driver_informed=False,   # the reply sets this. SPEC 3.6.
        audit=[{"at": now.isoformat(), "action": "OPENED", "event_id": event.id}],
    )]


def _already_open(state: TripState, event: OperationalEvent) -> bool:
    return any(
        exception.exception_type is event.event_type
        and exception.stop_id == event.stop_id
        and resolution.is_open(exception)
        for exception in state.exceptions
    )


def recompute_exposure(
    state: TripState, exception: OperationalException, now: datetime,
) -> OperationalException:
    """SPEC 3.6: `cost_exposure_paise` caches the detention maths and is
    recomputed from state, never accumulated in place. Returns a copy."""
    exposure = detention.exposure_paise(state, exception.stop_id, now)
    if exposure == exception.cost_exposure_paise:
        return exception
    return exception.model_copy(update={"cost_exposure_paise": exposure})
