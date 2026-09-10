"""Replay the seeded shift in memory: python scripts/replay_shift.py."""

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
import json
from pathlib import Path
import sys

# Also allow direct execution from outside the repository.
ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from app.contracts.enums import EventType
from app.contracts.event import OperationalEvent
from app.domain import detention, exception_rules, resolution, watchdog
from app.domain.clock import ONE_MINUTE, SimulatedClock
from app.domain.state_machine import Rejected, TripState, apply

SEED_DIRECTORY = ROOT / "app/data/seed"
FIXTURE_PATH = ROOT / "app/eval/fixtures/fixture_events.json"


def record(
    state: TripState,
    event: OperationalEvent,
    now: datetime,
    emit: Callable[[str], None],
    *,
    origin: str,
) -> TripState:
    state, outcome = apply(state, event, now)
    if isinstance(outcome, Rejected):
        raise ValueError(f"{now:%H:%M}  REJECTED  {event.id}: {outcome.reason}")

    for transition in outcome.transitions:
        emit(f"{now:%H:%M}  {state.vehicle_id} -> {transition.stop_id}  "
             f"{transition.after.value}")

    if event.event_type is EventType.DETENTION_CROSSED:
        waiting = detention.compute(state, event.stop_id, now)
        if waiting is not None:
            emit(f"{now:%H:%M}  DETENTION_CROSSED  "
                 f"billable={waiting.billable_minutes}min  stop={event.stop_id}")

    evaluation = exception_rules.evaluate(state, event, now)
    state = state.with_exceptions(evaluation.changed)
    closed = resolution.close_matching(state, event, now)
    state = state.with_exceptions(closed)
    for exception in evaluation.opened:
        emit(f"{now:%H:%M}  EXCEPTION OPENED  {exception.exception_type.value}  "
             f"stop={exception.stop_id or '-'}  ({origin})")
    for exception in evaluation.amended:
        emit(f"{now:%H:%M}  EXCEPTION UPDATED  {exception.exception_type.value}  "
             f"stop={exception.stop_id or '-'}  "
             f"exposure={exception.cost_exposure_paise}p")
    for exception in closed:
        emit(f"{now:%H:%M}  EXCEPTION RESOLVED  {exception.exception_type.value}  "
             f"stop={exception.stop_id or '-'}")

    if (not outcome.transitions and not evaluation.changed
            and event.event_type is not EventType.DETENTION_CROSSED):
        emit(f"{now:%H:%M}  {state.vehicle_id} -> {event.stop_id or '-'}  "
             f"{event.event_type.value}  ({origin})")
    return state


def replay(
    fixture_path: Path = FIXTURE_PATH,
    seed_directory: Path = SEED_DIRECTORY,
    emit: Callable[[str], None] = print,
) -> TripState:
    """Fixtures precede watchdog evaluation at the same minute; no persistence."""
    trips = json.loads((seed_directory / "trips.json").read_text(encoding="utf-8"))
    customers = json.loads((seed_directory / "customers.json").read_text(encoding="utf-8"))
    if len(trips) != 1:
        raise ValueError("The shift replay requires exactly one seeded trip")
    trip = trips[0]
    state = TripState.build(trip, trip["stops"], customers)
    clock = SimulatedClock(state.shift_start, state.shift_end, step=ONE_MINUTE)

    scheduled: dict[datetime, list[OperationalEvent]] = defaultdict(list)
    seen: set[str] = set()
    for row in json.loads(fixture_path.read_text(encoding="utf-8")):
        event = OperationalEvent.model_validate(row)
        if event.id in seen:
            raise ValueError(f"Duplicate fixture event id: {event.id}")
        seen.add(event.id)
        if (
            (event.trip_id, event.driver_id, event.vehicle_id)
            != (state.trip_id, state.driver_id, state.vehicle_id)
            or event.unresolved_fields
            or (event.stop_id is not None and state.stop(event.stop_id) is None)
        ):
            raise ValueError(f"Fixture identity does not match the seed: {event.id}")
        if (
            event.ingested_at.utcoffset() is None
            or event.occurred_at.utcoffset() is None
            or not state.is_open(event.ingested_at)
            or (event.ingested_at - clock.start) % ONE_MINUTE
        ):
            raise ValueError(f"Fixture must be ingested on a shift minute: {event.id}")
        scheduled[event.ingested_at].append(event)

    emit(f"{clock.now():%H:%M}  SHIFT STARTED  trip={state.trip_id}")
    for now in clock.ticks():
        # Preserve JSON order when multiple events share an ingestion time.
        for event in scheduled.get(now, ()):
            state = record(state, event, now, emit, origin=event.source)
        for event in watchdog.tick(state, now):
            state = record(state, event, now, emit, origin="watchdog")
        expired = resolution.expire_open(state, now)
        state = state.with_exceptions(expired)
        for exception in expired:
            emit(f"{now:%H:%M}  EXCEPTION EXPIRED  {exception.exception_type.value}  "
                 f"stop={exception.stop_id or '-'}")
    emit(f"{clock.now():%H:%M}  SHIFT ENDED  trip={state.trip_id}")
    return state


if __name__ == "__main__":
    replay()
