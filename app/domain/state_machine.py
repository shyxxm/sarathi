"""SPEC 4.1. Trip state, and the only code allowed to change it.

Pure: state in, values out. `now` is an argument, never a clock read.
See CLAUDE.md rules 4, 5 and 10.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict

from app.contracts.enums import EventType, Language, StopStatus
from app.contracts.event import OperationalEvent
from app.contracts.exception import OperationalException
from app.domain import words
# English, for the board, drafts and the responder's context. What a driver
# hears goes through `words` in his language (SPEC 7.2).
from app.domain.words import EVENT_WORDS, STATUS_PHRASE  # noqa: F401

AT_STOP = frozenset({StopStatus.ARRIVED, StopStatus.IN_SERVICE})
FINISHED = frozenset({StopStatus.COMPLETED, StopStatus.FAILED, StopStatus.REATTEMPT_SCHEDULED})


class CustomerTerms(BaseModel):
    """The standing terms Sarathi reasons about. SPEC 3, customers table."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    free_detention_minutes: int
    detention_rate_paise_per_min: int
    reattempt_cutoff_time: time
    site_contact: str
    gate_procedure: str
    delivery_window_policy: str


class StopState(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    seq: int
    customer_id: str
    planned_arrival: datetime
    planned_departure: datetime
    service_minutes: int
    window_open: datetime
    window_close: datetime
    status: StopStatus = StopStatus.PENDING


class TripState(BaseModel):
    """Everything domain/ is allowed to look at. Assembled by the caller from
    stored rows; nothing in here reaches out for more.

    Tuples throughout: a frozen model does not stop `state.events.append(...)`,
    and a replay that can be appended to in place is not a replay.
    """

    model_config = ConfigDict(frozen=True)

    trip_id: str
    driver_id: str
    vehicle_id: str
    shift_start: datetime
    shift_end: datetime
    stops: tuple[StopState, ...]
    customers: tuple[CustomerTerms, ...]
    events: tuple[OperationalEvent, ...] = ()
    exceptions: tuple[OperationalException, ...] = ()

    # The language he is spoken to in. His, from the driver record — not the
    # language of the message in front of us. One message is a bad witness:
    # romanised Malayalam reads as Hindi often enough that a per-message guess
    # had Sarathi answering a Malayalam speaker in three scripts at once.
    driver_language: Language = Language.EN

    @classmethod
    def build(
        cls,
        trip: Mapping,
        stops: Iterable[Mapping],
        customers: Iterable[Mapping],
        drivers: Iterable[Mapping] = (),
        events: Iterable[OperationalEvent] = (),
        exceptions: Iterable[OperationalException] = (),
    ) -> "TripState":
        """From plain rows — repository dicts or seed JSON. No I/O here."""
        driver = next((row for row in drivers if row["id"] == trip["driver_id"]), None)
        return cls(
            trip_id=trip["id"],
            driver_id=trip["driver_id"],
            vehicle_id=trip["vehicle_id"],
            # No driver row means we do not know his language. English is what
            # the built-in strings are written in, so it is the honest default
            # rather than a guess at his.
            driver_language=Language(driver["preferred_language"]) if driver else Language.EN,
            shift_start=trip["shift_start"],
            shift_end=trip["shift_end"],
            stops=tuple(sorted(
                (StopState.model_validate(stop) for stop in stops), key=lambda stop: stop.seq,
            )),
            customers=tuple(CustomerTerms.model_validate(row) for row in customers),
            events=tuple(events),
            exceptions=tuple(exceptions),
        )

    def stop(self, stop_id: str | None) -> StopState | None:
        return next((stop for stop in self.stops if stop.id == stop_id), None)

    def customer(self, customer_id: str) -> CustomerTerms:
        for customer in self.customers:
            if customer.id == customer_id:
                return customer
        raise ValueError(f"Trip state has no terms for customer {customer_id}")

    def customer_for(self, stop_id: str) -> CustomerTerms:
        stop = self.stop(stop_id)
        if stop is None:
            raise ValueError(f"Trip state has no stop {stop_id}")
        return self.customer(stop.customer_id)

    def current_stop(self) -> StopState | None:
        """Where the driver is, or is heading. The default subject of a message."""
        for wanted in (AT_STOP, {StopStatus.EN_ROUTE}, {StopStatus.PENDING}):
            found = next((stop for stop in self.stops if stop.status in wanted), None)
            if found is not None:
                return found
        return None

    def events_at(self, stop_id: str) -> tuple[OperationalEvent, ...]:
        return tuple(event for event in self.events if event.stop_id == stop_id)

    def last_driver_event(self) -> OperationalEvent | None:
        return next((event for event in reversed(self.events) if event.source == "driver"), None)

    def is_open(self, now: datetime) -> bool:
        return self.shift_start <= now <= self.shift_end

    def with_event(self, event: OperationalEvent) -> "TripState":
        return self.model_copy(update={"events": self.events + (event,)})

    def with_stop_status(self, stop_id: str, status: StopStatus) -> "TripState":
        stops = tuple(
            stop.model_copy(update={"status": status}) if stop.id == stop_id else stop
            for stop in self.stops
        )
        return self.model_copy(update={"stops": stops})

    def with_exceptions(self, exceptions: Iterable[OperationalException]) -> "TripState":
        """Merge by id — a closed copy replaces the open one it was made from."""
        merged = {exception.id: exception for exception in self.exceptions}
        merged.update({exception.id: exception for exception in exceptions})
        return self.model_copy(update={"exceptions": tuple(merged.values())})


@dataclass(frozen=True)
class Transition:
    stop_id: str
    before: StopStatus
    after: StopStatus


@dataclass(frozen=True)
class Applied:
    """The event is now part of the record. `transitions` may be empty: a gate
    closure is recorded against the stop without moving it."""

    event: OperationalEvent
    transitions: tuple[Transition, ...] = ()
    applied: bool = True


@dataclass(frozen=True)
class Rejected:
    """State is untouched. `reason` is written to be spoken to the driver —
    it says what we have, what he said, and asks. SPEC 4.1."""

    event: OperationalEvent
    reason: str
    applied: bool = False

    @property
    def unclear_event(self) -> OperationalEvent:
        """SPEC 4.1: an illegal transition becomes UNCLEAR."""
        return self.event.model_copy(update={"event_type": EventType.UNCLEAR})


Outcome = Applied | Rejected

# SPEC 4.1. Anything not listed here changes no status: problem events attach
# to the stop and open exceptions instead.
#
# SERVICE_STARTED is optional. "Delivered, leaving" straight after "reached" is
# how people actually talk, and a system that rejects it teaches the driver it
# is pedantic. ARRIVED -> COMPLETED is therefore legal.
#
# PENDING -> ARRIVED is not. That one almost always means we have the wrong
# stop, and asking which stop he means is the useful answer.
TRANSITIONS: Mapping[EventType, tuple[frozenset[StopStatus], StopStatus]] = {
    EventType.ARRIVED_STOP: (frozenset({StopStatus.EN_ROUTE}), StopStatus.ARRIVED),
    EventType.SERVICE_STARTED: (frozenset({StopStatus.ARRIVED}), StopStatus.IN_SERVICE),
    EventType.STOP_COMPLETED: (frozenset(AT_STOP), StopStatus.COMPLETED),
    EventType.DELIVERY_REFUSED: (frozenset(AT_STOP), StopStatus.FAILED),
    # Emitted when the SCHEDULE_REATTEMPT action executes, so a reattempt
    # reaches state through apply() like everything else (CLAUDE.md rule 4) and
    # shows up in the driver's own record of the day. A consignee who was never
    # there does not need a refusal invented first, so this is legal from the
    # stop he is standing at as well as from FAILED.
    EventType.REATTEMPT_SCHEDULED: (
        frozenset(AT_STOP | {StopStatus.FAILED}), StopStatus.REATTEMPT_SCHEDULED),
}


def apply(state: TripState, event: OperationalEvent, now: datetime) -> tuple[TripState, Outcome]:
    """Fold one event into the trip. Returns the new state and what happened.

    On rejection the state returned is the state passed in, unchanged.
    """
    if now > state.shift_end:
        return state, Rejected(event, _say(state, "reject.shift_closed"))

    if event.event_type is EventType.DEPARTED:
        return _depart(state, event)
    if event.event_type not in TRANSITIONS:
        return state.with_event(event), Applied(event)   # recorded, moves nothing

    stop = _subject(state, event)
    if stop is None:
        return state, Rejected(event, _say(state, "reject.which_stop"))

    allowed, after = TRANSITIONS[event.event_type]
    if stop.status is after:
        return state, Rejected(event, _already(state, stop))
    if stop.status not in allowed:
        return state, Rejected(event, _disagrees(state, stop, event))

    moved = state.with_stop_status(stop.id, after).with_event(event)
    return moved, Applied(event, (Transition(stop.id, stop.status, after),))


def _depart(state: TripState, event: OperationalEvent) -> tuple[TripState, Outcome]:
    """He has set off. From the depot or from a stop — the words do not say, so
    the stop's status does. SPEC 3.1 and 4.1.

    Leaving a stop closes nothing; it puts the next stop on the road.
    """
    stop = state.stop(event.stop_id)
    if stop is None:
        return state, Rejected(event, _say(state, "reject.leaving_from"))

    if stop.status is StopStatus.PENDING:
        # Nothing has been reached yet, so this is the depot and that stop is
        # the one he is now driving to.
        moved = state.with_stop_status(stop.id, StopStatus.EN_ROUTE).with_event(event)
        return moved, Applied(event, (Transition(stop.id, stop.status, StopStatus.EN_ROUTE),))

    if stop.status not in FINISHED:
        return state, Rejected(event, _disagrees(state, stop, event))

    following = next(
        (other for other in state.stops
         if other.seq > stop.seq and other.status is StopStatus.PENDING),
        None,
    )
    if following is None:
        return state.with_event(event), Applied(event)
    moved = state.with_stop_status(following.id, StopStatus.EN_ROUTE).with_event(event)
    return moved, Applied(event, (Transition(following.id, following.status, StopStatus.EN_ROUTE),))


def _subject(state: TripState, event: OperationalEvent) -> StopState | None:
    """Which stop the transition acts on."""
    return state.stop(event.stop_id)


def _say(state: TripState, key: str, **slots) -> str:
    """His words, in his language once its table is written (SPEC 7.2)."""
    return words.say(words.spoken(state.driver_language), key, **slots)


def _label(state: TripState, stop: StopState) -> str:
    return _say(state, "label.stop", seq=stop.seq, customer=state.customer(stop.customer_id).name)


def _already(state: TripState, stop: StopState) -> str:
    return _say(state, "reject.already", stop=_label(state, stop),
                status=_say(state, f"status.{stop.status.value}"))


def _disagrees(state: TripState, stop: StopState, event: OperationalEvent) -> str:
    if event.event_type is EventType.ARRIVED_STOP and stop.status is StopStatus.PENDING:
        return _wrong_stop(state, stop)
    key = f"said.{event.event_type.value}"
    said = _say(state, key if key in words.EN else "said.other")
    return _say(state, "reject.disagrees", stop=_label(state, stop),
                status=_say(state, f"status.{stop.status.value}"), said=said)


def _wrong_stop(state: TripState, stop: StopState) -> str:
    """He says he has reached a stop we have not sent him to yet. Nine times in
    ten we have the wrong stop, not the wrong driver, so ask him which."""
    here = state.current_stop()
    if here is None or here.id == stop.id:
        return _say(state, "reject.not_on_road")
    return _say(state, "reject.wrong_stop", here=_label(state, here), stop=_label(state, stop))
