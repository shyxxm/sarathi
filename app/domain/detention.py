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
    # When he said it. A claim without its time gets set against a running
    # count and reads as a disagreement it is not: "he says 8, we count 61"
    # is one number from 10:20 and one from 11:13.
    driver_claimed_at: datetime | None = None

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


def wait_ended_at(state: TripState, stop_id: str) -> datetime | None:
    """When the waiting stopped. None while he is still standing there.

    Unloading starting is what ends the wait — from then on he is working, not
    waiting, and SPEC 4.2 does not bill the customer for that. A stop that
    never got that far ends its wait when he drives away: a refusal, an absent
    consignee, a reattempt tomorrow.

    Only events after the arrival count. Setting off from the depot is filed
    against the stop he is driving towards, so stop 1 carries a `DEPARTED` from
    08:05 that has nothing to do with leaving stop 1 at 09:55.
    """
    started = arrival_observed_at(state, stop_id)
    if started is None:
        return None
    after_arrival = [
        event for event in state.events_at(stop_id) if event.ingested_at >= started
    ]
    ends = [
        event.ingested_at for event in after_arrival
        if event.event_type is EventType.SERVICE_STARTED
    ] or [
        event.ingested_at for event in after_arrival
        if event.event_type is EventType.DEPARTED
    ]
    return min(ends, default=None)


def latest_claim(state: TripState, stop_id: str) -> OperationalEvent | None:
    """The last message at this stop in which he said how long he had waited.

    The event rather than the number, because the number is only half of it:
    a figure he gave an hour ago is not a claim about now, and whatever reads
    it has to be able to tell the difference.
    """
    return next(
        (event for event in reversed(state.events_at(stop_id))
         if event.source == "driver" and event.driver_claimed_wait_minutes is not None),
        None,
    )


def claimed_wait_minutes(state: TripState, stop_id: str) -> int | None:
    """What the driver said he waited. Stored, spoken back, never billed from."""
    claim = latest_claim(state, stop_id)
    return claim.driver_claimed_wait_minutes if claim else None


def compute(state: TripState, stop_id: str, now: datetime) -> Detention | None:
    """SPEC 4.2, as written. None until an arrival has been observed.

    `now` is the instant the exposure is quoted for, so a ledger row written at
    departure passes the departure's `ingested_at` and freezes there.
    """
    observed_from = arrival_observed_at(state, stop_id)
    if observed_from is None:
        return None

    customer = state.customer_for(stop_id)
    claim = latest_claim(state, stop_id)
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
        driver_claimed_wait_minutes=claim.driver_claimed_wait_minutes if claim else None,
        driver_claimed_at=claim.ingested_at if claim else None,
    )


def exposure_paise(state: TripState, stop_id: str | None, now: datetime) -> int:
    """Recomputed from state every time. SPEC 3.6: never accumulated in place.

    Quoted at the end of the wait where there is one, so a card refreshed hours
    later reads the same figure the ledger billed. Quoting `now` unconditionally
    would make a resolved exception's exposure climb for the rest of the shift
    every time a board redrew it.
    """
    if stop_id is None:
        return 0
    waiting = compute(state, stop_id, wait_ended_at(state, stop_id) or now)
    return waiting.exposure_paise if waiting else 0


def ledger_entry(
    state: TripState,
    stop_id: str,
    source_event: OperationalEvent,
    now: datetime,
    *,
    freeze_at: datetime | None = None,
) -> dict | None:
    """A `detention_ledger` row. None until an arrival has been observed.

    Written by the `LOG_DETENTION` action and by nothing else. SPEC 4.2.

    The clock stops on its own: at `SERVICE_STARTED` if unloading has begun, at
    `DEPARTED` if he left without it ever starting, and not at all while he
    is still out there waiting — that row quotes `now` and keeps running.
    Passing `freeze_at` overrides all of it and pins the row to that instant.

    The default has to be the right one, because the caller that gets this
    wrong bills a customer for the driver's own unloading. `compute()` stays a
    plain function of the instant it is handed; the policy lives here.
    """
    quoted_at = freeze_at or wait_ended_at(state, stop_id) or now
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
