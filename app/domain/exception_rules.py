"""Which events open an exception, and what it costs while it is open.

An exception belongs to a trip and a stop. It never belongs to a person, it
carries no history beyond this trip, and nothing here counts anything about the
driver. See CLAUDE.md rule 1 and SPEC 3.6.
"""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Evaluation:
    """What an event did to the exception record at its stop.

    Opened and amended are kept apart because they read differently to a
    dispatcher and to the driver: one is a new problem, the other is more of
    what is already on the card.
    """

    opened: tuple[OperationalException, ...] = ()
    amended: tuple[OperationalException, ...] = ()

    @property
    def changed(self) -> tuple[OperationalException, ...]:
        return self.opened + self.amended


def evaluate(state: TripState, event: OperationalEvent, now: datetime) -> Evaluation:
    """What this event opens, or records on what is already open.

    Nothing at all if it opens none, and nothing if the same thing is already
    open at the same stop — one gate closure at stop 2 is one exception,
    however many times it is mentioned.
    """
    if event.event_type not in OPENS:
        return Evaluation()

    if event.event_type is EventType.DETENTION_CROSSED:
        # SPEC 4.3: the free time running out is a fact about the problem he is
        # already dealing with, not a second problem. The gate is shut; that is
        # what he is stuck behind. Opening a second card here would show a
        # driver-first tool double-counting his one bad hour.
        standing = _open_at(state, event.stop_id)
        if standing is not None:
            return Evaluation(amended=(_record_crossing(state, standing, event, now),))

    if _already_open(state, event):
        return Evaluation()

    return Evaluation(opened=(OperationalException(
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
    ),))


def _open_at(state: TripState, stop_id: str | None) -> OperationalException | None:
    """The problem he is already dealing with at that stop — the oldest one
    still live, which is the one the crossing is a fact about."""
    if stop_id is None:
        return None
    live = [
        exception for exception in state.exceptions
        if exception.stop_id == stop_id and resolution.is_open(exception)
    ]
    return min(live, key=lambda exception: exception.opened_at, default=None)


def _record_crossing(
    state: TripState,
    exception: OperationalException,
    event: OperationalEvent,
    now: datetime,
) -> OperationalException:
    """The crossing on the existing card: what it now costs, and a line saying
    when it crossed. A copy — domain/ mutates nothing it was handed."""
    waiting = detention.compute(state, event.stop_id, now)
    entry = {"at": now.isoformat(), "action": "DETENTION_CROSSED", "event_id": event.id}
    if waiting is not None:
        entry["billable_minutes"] = waiting.billable_minutes
    return exception.model_copy(update={
        "cost_exposure_paise": (
            waiting.exposure_paise if waiting else exception.cost_exposure_paise),
        "audit": [*exception.audit, entry],
    })


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
