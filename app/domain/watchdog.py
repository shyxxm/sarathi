"""SPEC 4.3. The four rules that notice when nobody has said anything.

These produce a check-in, never a report about a person. There is no count of
how often a rule fired, no per-driver tally, and nothing here reads a clock.
See CLAUDE.md rule 1.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from app.contracts.enums import EventType, StopStatus
from app.contracts.event import OperationalEvent
from app.domain import detention, resolution
from app.domain.constants import OVERDUE_GRACE, SILENCE_AFTER
from app.domain.state_machine import AT_STOP, StopState, TripState

NOT_YET_THERE = frozenset({StopStatus.PENDING, StopStatus.EN_ROUTE})


def tick(state: TripState, now: datetime) -> list[OperationalEvent]:
    """Every rule whose condition holds now and is not already latched.

    Rule order follows the SPEC 4.3 table so a replay log is stable.
    """
    if not state.is_open(now):
        return []

    fired: list[OperationalEvent] = []
    fired += _overdue(state, now)
    fired += _silence(state, now)
    fired += _window_risk(state, now)
    fired += _detention(state, now)
    return fired


# --- latching ---------------------------------------------------------------
#
# "Fires once per (stop, rule) unless the condition clears and recurs."
#
# Nothing tracks that in a set. The event log already records every firing, and
# SPEC 4.4 already says what clears each condition, so the latch is derived:
#
#     latched = a system event of this type at this stop exists, and no event
#               after it clears the condition.
#
# A fired-set would be state the replay has to carry and rebuild in the right
# order; this is a function of the log, so replaying the same log twice gives
# the same firings. Re-arming reuses resolution.closes(), which means the
# watchdog and the exception lifecycle can never drift apart on what "cleared"
# means.


def _latched(state: TripState, rule: EventType, stop_id: str | None) -> bool:
    last_fired = None
    for index, event in enumerate(state.events):
        if event.source == "system" and event.event_type is rule and event.stop_id == stop_id:
            last_fired = index
    if last_fired is None:
        return False
    return not any(_clears(state, rule, event, stop_id) for event in state.events[last_fired + 1:])


def _clears(
    state: TripState, rule: EventType, event: OperationalEvent, stop_id: str | None,
) -> bool:
    if rule is EventType.DETENTION_CROSSED:
        # Not in the SPEC 4.4 table: detention has no exception closure there.
        # A fresh arrival at the same stop restarts the observed wait, so the
        # condition has genuinely cleared and may cross again.
        return event.event_type is EventType.ARRIVED_STOP and event.stop_id == stop_id
    return resolution.closes(rule, event, stop_id=stop_id, stop=state.stop(stop_id))


def _emit(
    state: TripState, rule: EventType, stop_id: str | None, now: datetime,
) -> OperationalEvent:
    """System events happen and are learned at the same instant, so both
    timestamps are `now`. The id is derived, so a replay produces the same one."""
    scope = stop_id or state.trip_id
    return OperationalEvent(
        id=f"system-{rule.value}-{scope}-{int(now.timestamp())}",
        source="system",
        occurred_at=now,
        ingested_at=now,
        event_type=rule,
        driver_id=state.driver_id,
        vehicle_id=state.vehicle_id,
        trip_id=state.trip_id,
        stop_id=stop_id,
    )


def _fire(
    state: TripState, rule: EventType, stops: Iterable[StopState | None], now: datetime,
) -> list[OperationalEvent]:
    return [
        _emit(state, rule, stop.id if stop else None, now)
        for stop in stops
        if not _latched(state, rule, stop.id if stop else None)
    ]


# --- the four rules ---------------------------------------------------------


def _overdue(state: TripState, now: datetime) -> list[OperationalEvent]:
    """now > planned_arrival + grace, and he is not there yet."""
    return _fire(state, EventType.STOP_OVERDUE, [
        stop for stop in state.stops
        if stop.status in NOT_YET_THERE and now > stop.planned_arrival + OVERDUE_GRACE
    ], now)


def _silence(state: TripState, now: datetime) -> list[OperationalEvent]:
    """Ninety minutes without hearing from him. Attaches to where he was last
    known to be, or to the trip if he never reported anything."""
    last = state.last_driver_event()
    heard_at = last.ingested_at if last else state.shift_start
    if now - heard_at <= SILENCE_AFTER:
        return []
    at = state.current_stop()
    return _fire(state, EventType.DRIVER_SILENT, [at if at and at.status in AT_STOP else None], now)


def _window_risk(state: TripState, now: datetime) -> list[OperationalEvent]:
    return _fire(state, EventType.WINDOW_AT_RISK, [
        stop for stop in state.stops
        if stop.status in NOT_YET_THERE and projected_arrival(state, stop, now) > stop.window_close
    ], now)


def _detention(state: TripState, now: datetime) -> list[OperationalEvent]:
    """observed_wait > free_minutes, while he is still standing there."""
    crossed = []
    for stop in state.stops:
        if stop.status not in AT_STOP:
            continue
        waiting = detention.compute(state, stop.id, now)
        if waiting and waiting.crossed:
            crossed.append(stop)
    return _fire(state, EventType.DETENTION_CROSSED, crossed, now)


def projected_arrival(state: TripState, stop: StopState, now: datetime) -> datetime:
    """When he is likely to reach a stop he has not reached yet.

    From where he is: when he can leave there, plus the planned run onward.
    `planned_arrival` minus the origin's `planned_departure` already carries the
    intermediate stops and their service time with it.
    """
    here = state.current_stop()
    if here is None or here.seq >= stop.seq:
        return max(now, stop.planned_arrival)   # the stop he is heading for now

    if here.status in AT_STOP:
        arrived = detention.arrival_observed_at(state, here.id)
        served = (now - arrived).total_seconds() / 60 if arrived else 0.0
        leaves_at = now + timedelta(minutes=max(0.0, here.service_minutes - served))
    else:
        leaves_at = max(now, here.planned_arrival) + timedelta(minutes=here.service_minutes)
    return leaves_at + (stop.planned_arrival - here.planned_departure)
