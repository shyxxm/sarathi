"""Which driver, which trip, which stop. Resolved from state, never by the
model. See CLAUDE.md rule 3.

`resolve` returns a NEW event. `OperationalEvent` is frozen, but a frozen
pydantic model does not freeze the list inside it: `event.unresolved_fields
.append("stop_id")` would succeed, mutate an event another caller already holds,
and quietly break replay. Everything here goes through `model_copy(update=...)`
with a fresh list, and so must anything else in domain/ that amends an event.
"""

import re
from collections.abc import Sequence

from app.contracts.enums import EventType
from app.contracts.event import OperationalEvent
from app.domain.state_machine import StopState, TripState

STOP_ID = "stop_id"

# These are about the trip, not a stop. A missing stop on one of them is not an
# unresolved field.
TRIP_LEVEL = frozenset({
    EventType.DEPARTED_DEPOT,
    EventType.DRIVER_SILENT,
    EventType.VEHICLE_BREAKDOWN,
    EventType.ACKNOWLEDGEMENT,
    EventType.UNCLEAR,
})

ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "1": 1,
    "second": 2, "2nd": 2, "two": 2, "2": 2,
    "third": 3, "3rd": 3, "three": 3, "3": 3,
    "fourth": 4, "4th": 4, "four": 4, "4": 4,
    "fifth": 5, "5th": 5, "five": 5, "5": 5,
}


def resolve(event: OperationalEvent, state: TripState) -> OperationalEvent:
    """Fill the identity fields from current state. Never guesses a stop: an
    unresolvable or contradicted one is named in `unresolved_fields` and left
    empty, which the router turns into an ESCALATE (SPEC 5)."""
    unresolved = [field for field in event.unresolved_fields]   # never the caller's list

    stop_id = event.stop_id
    if stop_id is None:
        stop_id, uncertain = _resolve_stop(event, state)
        if uncertain and event.event_type not in TRIP_LEVEL and STOP_ID not in unresolved:
            unresolved.append(STOP_ID)

    return event.model_copy(update={
        "driver_id": state.driver_id,
        "vehicle_id": state.vehicle_id,
        "trip_id": state.trip_id,
        "stop_id": stop_id,
        "unresolved_fields": unresolved,
    })


def _resolve_stop(event: OperationalEvent, state: TripState) -> tuple[str | None, bool]:
    expected = state.current_stop()
    candidates = match_hint(state, event.location_hint)

    if not candidates:
        # Either he named no place, or he named one we cannot read. Neither
        # contradicts state, so the stop he is on stands.
        return (expected.id, False) if expected else (None, True)
    if len(candidates) > 1:
        return None, True
    only = candidates[0]
    if expected is None or only.id == expected.id:
        return only.id, False
    return None, True   # he named a different stop than the one we have him on


def match_hint(state: TripState, hint: str | None) -> Sequence[StopState]:
    """Stops a spoken place could mean. Sequence words ("second godown") and
    consignee names ("Malabar"), and only names distinctive enough to pick out
    one customer — "wholesale" appearing in two consignees identifies neither."""
    if not hint:
        return ()
    words = re.findall(r"[a-z0-9]+", hint.casefold())

    seqs = {ORDINALS[word] for word in words if word in ORDINALS}
    by_seq = {stop.id for stop in state.stops if stop.seq in seqs}

    named = {customer.id for customer in state.customers if _names(state, customer.id) & set(words)}
    by_name = {stop.id for stop in state.stops if stop.customer_id in named}

    if by_seq and by_name:
        both = by_seq & by_name
        matched = both or (by_seq | by_name)
    else:
        matched = by_seq or by_name
    return tuple(stop for stop in state.stops if stop.id in matched)


def _names(state: TripState, customer_id: str) -> set[str]:
    """Name words that belong to exactly one customer on this trip."""
    counts: dict[str, int] = {}
    for customer in state.customers:
        for word in set(re.findall(r"[a-z]{4,}", customer.name.casefold())):
            counts[word] = counts.get(word, 0) + 1
    customer = state.customer(customer_id)
    return {
        word for word in re.findall(r"[a-z]{4,}", customer.name.casefold())
        if counts[word] == 1
    }
