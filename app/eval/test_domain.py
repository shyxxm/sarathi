"""The deterministic core, against the seeded trip. No database, no clock, no
network — SPEC 4 arithmetic and nothing else."""

from datetime import datetime, timedelta
import json

import pytest

from app.contracts.enums import EventType, ExceptionStatus, Language, StopStatus
from app.contracts.event import OperationalEvent
from app.data.repository import SEED_DIRECTORY
from app.domain import detention, exception_rules, identity, resolution, watchdog
from app.domain.state_machine import Applied, Rejected, TripState, apply


def seed(name):
    return json.loads((SEED_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def state():
    trip = seed("trips")[0]
    return TripState.build(trip, trip["stops"], seed("customers"), seed("drivers"))


def at(hour, minute):
    return datetime.fromisoformat(f"2026-09-10T{hour:02d}:{minute:02d}:00+05:30")


def event(event_type, occurred, ingested=None, *, source="driver", **changes):
    return OperationalEvent(
        id=changes.pop("id", f"event-{event_type.value}-{occurred:%H%M}"),
        source=source, occurred_at=occurred, ingested_at=ingested or occurred,
        event_type=event_type, **changes,
    )


def run(state, *events):
    outcomes = []
    for one in events:
        state, outcome = apply(state, one, one.ingested_at)
        outcomes.append(outcome)
    return state, outcomes


def arrive(state, stop_id, when):
    return run(
        state,
        event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"),
        event(EventType.ARRIVED_STOP, when, stop_id=stop_id),
    )[0]


# --- 4.1 state machine ------------------------------------------------------


def test_a_stop_walks_the_whole_chain_and_departure_puts_the_next_on_the_road(state):
    state, outcomes = run(
        state,
        event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"),
        event(EventType.ARRIVED_STOP, at(9, 2), stop_id="stop-1"),
        event(EventType.SERVICE_STARTED, at(9, 10), stop_id="stop-1"),
        event(EventType.STOP_COMPLETED, at(9, 35), stop_id="stop-1"),
        event(EventType.DEPARTED, at(9, 40), stop_id="stop-1"),
    )
    assert all(isinstance(outcome, Applied) for outcome in outcomes)
    assert state.stop("stop-1").status is StopStatus.COMPLETED
    assert state.stop("stop-2").status is StopStatus.EN_ROUTE
    assert outcomes[-1].transitions[0].stop_id == "stop-2"
    assert len(state.events) == 5


def test_an_illegal_transition_changes_nothing_and_asks_the_driver(state):
    state, _ = run(state, event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"))
    before = state
    jump = event(EventType.STOP_COMPLETED, at(9, 30), stop_id="stop-1")

    after, outcome = apply(state, jump, at(9, 30))

    assert isinstance(outcome, Rejected)
    assert after is before
    assert after.stop("stop-1").status is StopStatus.EN_ROUTE
    assert after.events == before.events
    assert "stop 1" in outcome.reason and "Periyar Wholesale Foods" in outcome.reason
    assert outcome.reason.endswith("?")
    assert outcome.unclear_event.event_type is EventType.UNCLEAR
    assert jump.event_type is EventType.STOP_COMPLETED   # the original is untouched


def test_delivered_straight_after_reached_is_legal(state):
    """SERVICE_STARTED is optional. "Delivered, leaving" is not an error."""
    state = arrive(state, "stop-1", at(9, 2))
    state, outcomes = run(state, event(EventType.STOP_COMPLETED, at(9, 30), stop_id="stop-1"))

    assert isinstance(outcomes[0], Applied)
    assert state.stop("stop-1").status is StopStatus.COMPLETED
    assert outcomes[0].transitions[0].before is StopStatus.ARRIVED


def test_arriving_at_a_stop_we_have_not_sent_him_to_asks_which_stop(state):
    state, _ = run(state, event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"))   # stop-1 EN_ROUTE

    after, outcome = apply(state, event(EventType.ARRIVED_STOP, at(9, 2), stop_id="stop-3"),
                           at(9, 2))

    assert isinstance(outcome, Rejected)
    assert after is state
    assert outcome.reason == (
        "I have you on the way to stop 1, Periyar Wholesale Foods, "
        "not stop 3, Malabar Electrical Supplies. Which stop have you reached?"
    )


def test_arriving_before_we_have_him_on_the_road_asks_about_the_depot(state):
    _, outcome = apply(state, event(EventType.ARRIVED_STOP, at(9, 2), stop_id="stop-1"), at(9, 2))
    assert outcome.reason == "I do not have you on the road yet. Did you leave the depot?"


def test_repeating_an_event_is_rejected_as_something_we_already_have(state):
    state = arrive(state, "stop-1", at(9, 2))
    _, outcome = apply(state, event(EventType.ARRIVED_STOP, at(9, 5), stop_id="stop-1"), at(9, 5))
    assert isinstance(outcome, Rejected)
    assert "already" in outcome.reason


def test_a_problem_event_is_recorded_without_moving_the_stop(state):
    state = arrive(state, "stop-1", at(9, 2))
    state, outcomes = run(state, event(EventType.GATE_CLOSED, at(9, 6), stop_id="stop-1"))
    assert isinstance(outcomes[0], Applied) and outcomes[0].transitions == ()
    assert state.stop("stop-1").status is StopStatus.ARRIVED


# --- identity ---------------------------------------------------------------


def test_resolve_fills_identity_from_state_and_never_touches_the_event(state):
    state = arrive(state, "stop-1", at(9, 2))
    raw = event(EventType.GATE_CLOSED, at(9, 6))

    resolved = identity.resolve(raw, state)

    assert (resolved.driver_id, resolved.trip_id, resolved.stop_id) == (
        "driver-1", "trip-1", "stop-1")
    assert raw.driver_id is None and raw.stop_id is None
    assert resolved.unresolved_fields is not raw.unresolved_fields


def test_a_hint_naming_another_stop_is_left_unresolved_not_guessed(state):
    state = arrive(state, "stop-1", at(9, 2))
    raw = event(EventType.GATE_CLOSED, at(9, 6), location_hint="Malabar Electrical gate two")

    resolved = identity.resolve(raw, state)

    assert resolved.stop_id is None
    assert resolved.unresolved_fields == ["stop_id"]
    assert raw.unresolved_fields == []


def test_a_hint_that_could_be_two_stops_is_unresolved(state):
    state = arrive(state, "stop-1", at(9, 2))
    # Kochi Homeware is stop 2 and stop 5. "Second" disambiguates it.
    ambiguous = identity.resolve(
        event(EventType.GATE_CLOSED, at(9, 6), location_hint="Kochi Homeware"), state)
    # Named, not silently None: "unresolved" forces ESCALATE at M3, whereas a
    # bare None is indistinguishable from "he gave no hint" and would not.
    assert ambiguous.stop_id is None
    assert ambiguous.unresolved_fields == ["stop_id"]
    assert identity.resolve(
        event(EventType.GATE_CLOSED, at(9, 6), location_hint="second godown, Homeware"), state,
    ).unresolved_fields == ["stop_id"]   # contradicts stop-1, still not guessed


def test_a_trip_level_event_needs_no_stop(state):
    resolved = identity.resolve(event(EventType.VEHICLE_BREAKDOWN, at(8, 40),
                                      location_hint="somewhere on the bypass"), state)
    assert resolved.unresolved_fields == []


# --- 4.2 detention ----------------------------------------------------------


def at_stop_two(state):
    """Stop 1 done and left, standing at stop 2. He says he pulled in at 10:20;
    we only heard about it at 10:30."""
    state, _ = run(
        state,
        event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"),
        event(EventType.ARRIVED_STOP, at(9, 0), stop_id="stop-1"),
        event(EventType.STOP_COMPLETED, at(9, 35), stop_id="stop-1"),
        event(EventType.DEPARTED, at(9, 40), stop_id="stop-1"),
        event(EventType.ARRIVED_STOP, at(10, 20), at(10, 30),
              stop_id="stop-2", driver_claimed_wait_minutes=40),
    )
    return state


def test_detention_arithmetic(state):
    waiting = detention.compute(at_stop_two(state), "stop-2", at(11, 31))

    assert waiting.arrival_observed_at == at(10, 30)      # ingested, not occurred
    assert waiting.observed_wait_minutes == 61
    assert waiting.free_detention_minutes == 60
    assert waiting.billable_minutes == 1
    assert waiting.exposure_paise == 150
    assert waiting.driver_claimed_wait_minutes == 40      # kept, never billed from
    # And when he said it. A figure without its time gets set against a clock
    # that has been running since and reads as a disagreement it is not.
    assert waiting.driver_claimed_at == at(10, 30)
    assert detention.compute(at_stop_two(state), "stop-2", at(11, 0)).billable_minutes == 0
    assert detention.compute(at_stop_two(state), "stop-3", at(11, 31)) is None


def test_a_stop_he_never_gave_a_figure_for_carries_no_claim(state):
    state, _ = run(
        state,
        event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"),
        event(EventType.ARRIVED_STOP, at(9, 0), stop_id="stop-1"),
    )
    waiting = detention.compute(state, "stop-1", at(9, 30))
    assert waiting.driver_claimed_wait_minutes is None
    assert waiting.driver_claimed_at is None


def test_the_driver_is_spoken_to_in_his_own_language(state):
    """From the driver record, not from whatever one transcript looked like."""
    assert state.driver_language is Language.ML
    trip = seed("trips")[0]
    unknown = TripState.build(trip, trip["stops"], seed("customers"))
    assert unknown.driver_language is Language.EN


def test_the_ledger_stops_the_clock_when_unloading_starts(state):
    """He waited 62 minutes and then unloaded for 23. The customer is billed for
    the 62. Freezing at departure instead would bill him for the driver's own
    unloading — which is what the seeded shift does at stop-2, 22 minutes of it."""
    state = at_stop_two(state)                          # arrival ingested 10:30
    state, _ = run(
        state,
        event(EventType.SERVICE_STARTED, at(11, 32), stop_id="stop-2"),
        event(EventType.STOP_COMPLETED, at(11, 55), stop_id="stop-2"),
        event(EventType.DEPARTED, at(11, 56), stop_id="stop-2"),
    )
    departure = state.events[-1]

    assert detention.wait_ended_at(state, "stop-2") == at(11, 32)
    row = detention.ledger_entry(state, "stop-2", departure, at(15, 0))

    assert row["computed_at"] == at(11, 32)             # not 11:56
    assert row["billable_minutes"] == 2                 # 62 waited, 60 free
    assert row["exposure_paise"] == 300
    assert row["arrival_observed_at"] == at(10, 30)
    assert row["id"] == "detention-trip-1-stop-2"


def test_a_stop_that_never_unloaded_freezes_at_departure(state):
    """Nobody was there, so nothing was unloaded. The wait ends when he leaves."""
    state = arrive(state, "stop-1", at(9, 0))           # customer-1: 30 free, 250/min
    state, _ = run(
        state,
        event(EventType.CONSIGNEE_ABSENT, at(9, 20), stop_id="stop-1"),
        event(EventType.REATTEMPT_SCHEDULED, at(9, 50), source="system", stop_id="stop-1"),
        event(EventType.DEPARTED, at(9, 55), stop_id="stop-1"),
    )
    departure = state.events[-1]

    assert detention.wait_ended_at(state, "stop-1") == at(9, 55)
    row = detention.ledger_entry(state, "stop-1", departure, at(15, 0))

    assert row["computed_at"] == at(9, 55)
    assert row["billable_minutes"] == 25                # 55 waited, 30 free
    assert row["exposure_paise"] == 6250


def test_a_resolved_exception_stops_costing_more_every_time_it_is_read(state):
    """The dispatcher board redraws a card at 18:00 that was resolved at 11:32.
    It must read what the ledger billed, not what the clock has done since."""
    state = at_stop_two(state)                          # arrival ingested 10:30
    gate = event(EventType.GATE_CLOSED, at(10, 31), stop_id="stop-2")
    state, _ = run(state, gate)
    state = state.with_exceptions(exception_rules.evaluate(state, gate, at(10, 31)).opened)

    crossing = watchdog.tick(state, at(11, 31))[-1]
    state, _ = run(state, crossing)
    state = state.with_exceptions(
        exception_rules.evaluate(state, crossing, at(11, 31)).changed)

    started = event(EventType.SERVICE_STARTED, at(11, 32), stop_id="stop-2")
    state, _ = run(state, started)
    state = state.with_exceptions(resolution.close_matching(state, started, at(11, 32)))
    assert state.exceptions[0].status is ExceptionStatus.RESOLVED

    at_close = exception_rules.recompute_exposure(state, state.exceptions[0], at(18, 0))

    assert detention.compute(state, "stop-2", at(11, 32)).billable_minutes == 2
    assert detention.exposure_paise(state, "stop-2", at(18, 0)) == 2 * 150
    assert at_close.cost_exposure_paise == 300          # not 408 minutes of it
    assert at_close.cost_exposure_paise == exception_rules.recompute_exposure(
        state, state.exceptions[0], at(11, 32)).cost_exposure_paise


def test_a_stop_still_waiting_keeps_running_and_freeze_at_overrides(state):
    state = at_stop_two(state)
    standing = event(EventType.GATE_CLOSED, at(11, 31), stop_id="stop-2")

    assert detention.wait_ended_at(state, "stop-2") is None
    # Nothing has ended the wait, so the card shows it still running.
    assert detention.ledger_entry(state, "stop-2", standing, at(11, 31))["billable_minutes"] == 1
    assert detention.ledger_entry(state, "stop-2", standing, at(15, 0))["billable_minutes"] == 210
    # An explicit freeze_at still wins over everything.
    assert detention.ledger_entry(state, "stop-2", standing, at(15, 0),
                                  freeze_at=at(11, 31))["billable_minutes"] == 1
    assert detention.ledger_entry(state, "stop-3", standing, at(15, 0)) is None


def test_the_wait_is_anchored_on_arrival_not_on_the_problem_report(state):
    """He arrives, and a minute later says the gate is shut. The free time runs
    from the arrival. Anchoring on the problem report would quietly hand the
    customer a free minute for every minute the driver took to speak up."""
    state = at_stop_two(state)                      # arrival ingested 10:30
    gate = event(EventType.GATE_CLOSED, at(10, 31), stop_id="stop-2")
    state, _ = run(state, gate)

    assert detention.compute(state, "stop-2", at(11, 31)).arrival_observed_at == at(10, 30)
    assert fired(watchdog.tick(state, at(11, 30)), EventType.DETENTION_CROSSED) == []
    crossed = fired(watchdog.tick(state, at(11, 31)), EventType.DETENTION_CROSSED)
    assert [one.stop_id for one in crossed] == ["stop-2"]   # 60 min after 10:30, not 10:31


def test_the_same_wait_costs_differently_per_customer(state):
    state = arrive(state, "stop-1", at(10, 0))           # customer-1: 30 free, 250 paise
    assert detention.compute(state, "stop-1", at(11, 0)).exposure_paise == 30 * 250


# --- 4.3 watchdog -----------------------------------------------------------


def fired(events, rule):
    return [one for one in events if one.event_type is rule]


def test_overdue_fires_once_and_stops_once_he_arrives(state):
    state, _ = run(state, event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"))

    first = watchdog.tick(state, at(9, 16))
    assert [one.stop_id for one in fired(first, EventType.STOP_OVERDUE)] == ["stop-1"]

    state = state.with_event(first[0])
    assert fired(watchdog.tick(state, at(9, 20)), EventType.STOP_OVERDUE) == []

    state, _ = run(state, event(EventType.ARRIVED_STOP, at(9, 30), stop_id="stop-1"))
    assert fired(watchdog.tick(state, at(9, 40)), EventType.STOP_OVERDUE) == []


def test_a_rule_fires_again_when_the_condition_clears_and_recurs(state):
    state, _ = run(state, event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"))

    first = fired(watchdog.tick(state, at(9, 40)), EventType.DRIVER_SILENT)
    assert len(first) == 1                                   # 90 min since shift start

    state = state.with_event(first[0])
    assert fired(watchdog.tick(state, at(9, 45)), EventType.DRIVER_SILENT) == []

    state, _ = run(state, event(EventType.ARRIVED_STOP, at(9, 50), stop_id="stop-1"))
    assert fired(watchdog.tick(state, at(10, 0)), EventType.DRIVER_SILENT) == []

    again = fired(watchdog.tick(state, at(11, 21)), EventType.DRIVER_SILENT)
    assert len(again) == 1 and again[0].id != first[0].id


def test_detention_crossing_fires_while_he_is_still_standing_there(state):
    state = arrive(state, "stop-1", at(9, 0))                # 30 free minutes

    assert fired(watchdog.tick(state, at(9, 25)), EventType.DETENTION_CROSSED) == []
    crossed = fired(watchdog.tick(state, at(9, 31)), EventType.DETENTION_CROSSED)
    assert [one.stop_id for one in crossed] == ["stop-1"]
    assert crossed[0].source == "system" and crossed[0].occurred_at == crossed[0].ingested_at

    state = state.with_event(crossed[0])
    assert fired(watchdog.tick(state, at(10, 0)), EventType.DETENTION_CROSSED) == []


def test_window_risk_fires_when_the_projection_passes_the_close(state):
    state = arrive(state, "stop-1", at(9, 0))
    # Still at stop 1 at 11:30: stop 2 closes at 12:00 and is an hour off yet.
    risky = fired(watchdog.tick(state, at(11, 30)), EventType.WINDOW_AT_RISK)
    assert "stop-2" in {one.stop_id for one in risky}
    assert fired(watchdog.tick(state, at(9, 5)), EventType.WINDOW_AT_RISK) == []


def test_the_watchdog_is_quiet_outside_the_shift(state):
    assert watchdog.tick(state, at(19, 0)) == []


# --- exceptions and 4.4 closure --------------------------------------------


def test_an_exception_opens_once_per_stop_and_carries_the_exposure(state):
    state = arrive(state, "stop-1", at(9, 0))
    gate = event(EventType.GATE_CLOSED, at(9, 40), stop_id="stop-1")
    state, _ = run(state, gate)

    opened = exception_rules.evaluate(state, gate, at(9, 40)).opened
    assert len(opened) == 1
    assert opened[0].stop_id == "stop-1" and opened[0].opened_by == "driver"
    assert opened[0].status is ExceptionStatus.OPEN
    assert opened[0].driver_informed is False
    assert opened[0].cost_exposure_paise == 10 * 250        # 40 waited, 30 free

    state = state.with_exceptions(opened)
    again = event(EventType.GATE_CLOSED, at(9, 50), stop_id="stop-1", id="event-again")
    assert exception_rules.evaluate(state, again, at(9, 50)).changed == ()


def test_a_crossing_is_recorded_on_the_problem_he_is_already_stuck_behind(state):
    state = at_stop_two(state)
    gate = event(EventType.GATE_CLOSED, at(10, 31), stop_id="stop-2")
    state, _ = run(state, gate)
    state = state.with_exceptions(exception_rules.evaluate(state, gate, at(10, 31)).opened)

    crossing = watchdog.tick(state, at(11, 31))[-1]
    state, _ = run(state, crossing)
    evaluation = exception_rules.evaluate(state, crossing, at(11, 31))

    assert evaluation.opened == ()                       # one problem, one card
    assert [one.exception_type for one in evaluation.amended] == [EventType.GATE_CLOSED]
    amended = evaluation.amended[0]
    assert amended.id == state.exceptions[0].id
    assert amended.cost_exposure_paise == 150            # 1 billable minute at 150
    assert amended.audit[-1]["action"] == "DETENTION_CROSSED"
    assert amended.audit[-1]["billable_minutes"] == 1
    assert state.exceptions[0].cost_exposure_paise == 0  # the original is untouched


def test_a_crossing_with_nothing_open_at_the_stop_opens_its_own(state):
    state = at_stop_two(state)
    crossing = watchdog.tick(state, at(11, 31))[-1]
    state, _ = run(state, crossing)

    opened = exception_rules.evaluate(state, crossing, at(11, 31)).opened
    assert [one.exception_type for one in opened] == [EventType.DETENTION_CROSSED]
    assert opened[0].opened_by == "system"


def test_a_crossing_closes_when_service_starts_and_does_not_fire_again(state):
    state = at_stop_two(state)
    crossing = watchdog.tick(state, at(11, 31))[-1]
    state, _ = run(state, crossing)
    state = state.with_exceptions(exception_rules.evaluate(state, crossing, at(11, 31)).opened)

    started = event(EventType.SERVICE_STARTED, at(11, 32), stop_id="stop-2")
    state, _ = run(state, started)
    closed = resolution.close_matching(state, started, at(11, 32))

    assert [one.exception_type for one in closed] == [EventType.DETENTION_CROSSED]
    assert state.stop("stop-2").status is StopStatus.IN_SERVICE
    # Closing the exception does not restart the wait, so the rule stays latched:
    # he is still standing there and still over the free time.
    assert fired(watchdog.tick(state, at(11, 40)), EventType.DETENTION_CROSSED) == []


def test_a_crossing_also_closes_on_departure(state):
    state = at_stop_two(state)
    crossing = watchdog.tick(state, at(11, 31))[-1]
    state, _ = run(state, crossing)
    state = state.with_exceptions(exception_rules.evaluate(state, crossing, at(11, 31)).opened)

    state, _ = run(
        state,
        event(EventType.STOP_COMPLETED, at(11, 35), stop_id="stop-2"),
        event(EventType.DEPARTED, at(11, 36), stop_id="stop-2"),
    )
    left = event(EventType.DEPARTED, at(11, 36), stop_id="stop-2")
    assert len(resolution.close_matching(state, left, at(11, 36))) == 1
    assert resolution.expire_open(
        state.with_exceptions(resolution.close_matching(state, left, at(11, 36))),
        at(18, 0)) == []


def test_progress_events_open_nothing(state):
    state = arrive(state, "stop-1", at(9, 0))
    assert exception_rules.evaluate(
        state, event(EventType.SERVICE_STARTED, at(9, 5), stop_id="stop-1"), at(9, 5)
    ).changed == ()


def test_service_closes_a_gate_closure_but_leaves_approval_pending(state):
    state = arrive(state, "stop-1", at(9, 0))
    gate = event(EventType.GATE_CLOSED, at(9, 40), stop_id="stop-1")
    state, _ = run(state, gate)
    state = state.with_exceptions(exception_rules.evaluate(state, gate, at(9, 40)).opened)

    started = event(EventType.SERVICE_STARTED, at(10, 4), stop_id="stop-1")
    state, _ = run(state, started)
    closed = resolution.close_matching(state, started, at(10, 4))

    assert len(closed) == 1
    assert closed[0].status is ExceptionStatus.RESOLVED
    assert closed[0].resolution_status.value == "PENDING"    # a human approves. Rule 9.
    assert closed[0].resolving_event_id == started.id
    assert state.exceptions[0].status is ExceptionStatus.OPEN   # the original is untouched


def test_a_gate_closure_at_another_stop_is_not_closed(state):
    state = arrive(state, "stop-1", at(9, 0))
    gate = event(EventType.GATE_CLOSED, at(9, 40), stop_id="stop-1")
    state = state.with_event(gate).with_exceptions(
        exception_rules.evaluate(state, gate, at(9, 40)).opened)
    elsewhere = event(EventType.STOP_COMPLETED, at(10, 4), stop_id="stop-3")
    assert resolution.close_matching(state, elsewhere, at(10, 4)) == []


def test_silence_is_closed_by_anything_the_driver_says(state):
    state, _ = run(state, event(EventType.DEPARTED, at(8, 5), stop_id="stop-1"))
    quiet = watchdog.tick(state, at(9, 40))[-1]
    state = state.with_event(quiet).with_exceptions(
        exception_rules.evaluate(state, quiet, at(9, 40)).opened)
    assert state.exceptions[0].opened_by == "system"

    chat = event(EventType.ACKNOWLEDGEMENT, at(9, 50))
    assert len(resolution.close_matching(state, chat, at(9, 50))) == 1


def test_a_reattempt_reaches_state_as_an_event_and_closes_the_absence(state):
    state = arrive(state, "stop-1", at(9, 0))
    absent = event(EventType.CONSIGNEE_ABSENT, at(9, 20), stop_id="stop-1")
    state, _ = run(state, absent)
    state = state.with_exceptions(exception_rules.evaluate(state, absent, at(9, 20)).opened)

    # No refusal is invented first: nobody refused anything, nobody was there.
    reattempt = event(EventType.REATTEMPT_SCHEDULED, at(9, 50),
                      source="system", stop_id="stop-1")
    state, outcomes = run(state, reattempt)

    assert isinstance(outcomes[0], Applied)
    assert state.stop("stop-1").status is StopStatus.REATTEMPT_SCHEDULED
    assert reattempt in state.events            # it is in his record of the day
    closed = resolution.close_matching(state, reattempt, at(9, 50))
    assert [one.exception_type for one in closed] == [EventType.CONSIGNEE_ABSENT]
    assert exception_rules.evaluate(state, reattempt, at(9, 50)).changed == ()


def test_whatever_is_still_open_expires_at_trip_close(state):
    state = arrive(state, "stop-1", at(9, 0))
    gate = event(EventType.GATE_CLOSED, at(9, 40), stop_id="stop-1")
    state = state.with_event(gate).with_exceptions(
        exception_rules.evaluate(state, gate, at(9, 40)).opened)

    assert resolution.expire_open(state, at(17, 0)) == []
    expired = resolution.expire_open(state, at(18, 0))
    assert [one.status for one in expired] == [ExceptionStatus.EXPIRED]
