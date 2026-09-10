"""SPEC 4.2. The clock the customer is billed from, and the clock the driver
believes. Both are kept; only one of them bills."""

from dataclasses import dataclass
from datetime import datetime

from app.contracts.enums import EventType
from app.contracts.event import OperationalEvent
from app.domain.state_machine import TripState


@dataclass(frozen=True)
class Detention:
    stop_id: str
    customer_id: str
    arrival_observed_at: datetime
    observed_wait_minutes: int
    free_detention_minutes: int
    billable_minutes: int
    detention_rate_paise_per_min: int
    exposure_paise: int
    driver_claimed_wait_minutes: int | None = None

    @property
    def crossed(self) -> bool:
        return self.observed_wait_minutes > self.free_detention_minutes


def arrival_observed_at(state: TripState, stop_id: str) -> datetime | None:
    """SPEC 4.2: the `ingested_at` of the first ARRIVED_STOP at that stop.

    Not `occurred_at`. The driver's account of when he pulled in is his; the
    clock the customer is billed from starts when we learned about it.
    """
    arrivals = [
        event for event in state.events_at(stop_id)
        if event.event_type is EventType.ARRIVED_STOP
    ]
    return min((event.ingested_at for event in arrivals), default=None)


def claimed_wait_minutes(state: TripState, stop_id: str) -> int | None:
    """What the driver said he waited. Stored, spoken back, never billed from."""
    claims = [
        event.driver_claimed_wait_minutes for event in state.events_at(stop_id)
        if event.source == "driver" and event.driver_claimed_wait_minutes is not None
    ]
    return claims[-1] if claims else None


def compute(state: TripState, stop_id: str, now: datetime) -> Detention | None:
    """SPEC 4.2, as written. None until an arrival has been observed.

    `now` is the instant the exposure is quoted for, so a ledger row written at
    departure passes the departure's `ingested_at` and freezes there.
    """
    observed_from = arrival_observed_at(state, stop_id)
    if observed_from is None:
        return None

    customer = state.customer_for(stop_id)
    observed_wait = max(0, int((now - observed_from).total_seconds() // 60))
    billable_minutes = max(0, observed_wait - customer.free_detention_minutes)
    return Detention(
        stop_id=stop_id,
        customer_id=customer.id,
        arrival_observed_at=observed_from,
        observed_wait_minutes=observed_wait,
        free_detention_minutes=customer.free_detention_minutes,
        billable_minutes=billable_minutes,
        detention_rate_paise_per_min=customer.detention_rate_paise_per_min,
        exposure_paise=billable_minutes * customer.detention_rate_paise_per_min,
        driver_claimed_wait_minutes=claimed_wait_minutes(state, stop_id),
    )


def exposure_paise(state: TripState, stop_id: str | None, now: datetime) -> int:
    """Recomputed from state every time. SPEC 3.6: never accumulated in place."""
    if stop_id is None:
        return 0
    detention = compute(state, stop_id, now)
    return detention.exposure_paise if detention else 0


def ledger_entry(
    state: TripState,
    stop_id: str,
    source_event: OperationalEvent,
    now: datetime,
    *,
    freeze_at: datetime | None = None,
) -> dict | None:
    """A `detention_ledger` row. None until an arrival has been observed.

    `freeze_at` stops the clock. A row written on departure passes that
    departure's `ingested_at` and the exposure is fixed there; a row written
    while he is still standing at the gate passes nothing and quotes `now`.
    The choice is the caller's and visible at the call site — `compute()` stays
    a plain function of the instant it is handed, with no policy inside it.
    """
    quoted_at = freeze_at or now
    waiting = compute(state, stop_id, quoted_at)
    if waiting is None:
        return None
    return {
        "id": f"detention-{state.trip_id}-{stop_id}",
        "trip_id": state.trip_id,
        "stop_id": stop_id,
        "source_event_id": source_event.id,
        "arrival_observed_at": waiting.arrival_observed_at,
        "computed_at": quoted_at,
        "free_detention_minutes": waiting.free_detention_minutes,
        "billable_minutes": waiting.billable_minutes,
        "detention_rate_paise_per_min": waiting.detention_rate_paise_per_min,
        "exposure_paise": waiting.exposure_paise,
        "driver_claimed_wait_minutes": waiting.driver_claimed_wait_minutes,
    }
