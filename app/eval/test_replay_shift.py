"""Replay integration: fixture timing, legal transitions, and domain lifecycle."""

from datetime import timedelta
import json
import subprocess
import sys

import pytest

from app.contracts.enums import EventType, ExceptionStatus, StopStatus
from scripts import replay_shift


def test_replay_walks_every_minute_and_covers_the_four_scenarios(monkeypatch):
    ticks = []
    tick = replay_shift.watchdog.tick

    def observe(state, now):
        ticks.append(now)
        # The late arrival report must be applied at ingestion, before tick().
        if now.strftime("%H:%M") == "10:11":
            assert state.stop("stop-2").status is StopStatus.EN_ROUTE
        if now.strftime("%H:%M") == "10:12":
            assert state.stop("stop-2").status is StopStatus.ARRIVED
        return tick(state, now)

    monkeypatch.setattr(replay_shift.watchdog, "tick", observe)
    lines = []
    state = replay_shift.replay(emit=lines.append)

    assert ticks == [state.shift_start + timedelta(minutes=n) for n in range(601)]
    assert ticks[-1] == state.shift_end
    assert [stop.status for stop in state.stops] == [
        StopStatus.COMPLETED, StopStatus.COMPLETED, StopStatus.REATTEMPT_SCHEDULED,
        StopStatus.COMPLETED, StopStatus.COMPLETED,
    ]
    assert not any(exception.stop_id == "stop-1" for exception in state.exceptions)
    assert "10:13  EXCEPTION OPENED  GATE_CLOSED  stop=stop-2  (driver)" in lines
    assert "11:13  DETENTION_CROSSED  billable=1min  stop=stop-2" in lines
    assert "11:14  EXCEPTION RESOLVED  GATE_CLOSED  stop=stop-2" in lines
    assert "12:10  EXCEPTION RESOLVED  CONSIGNEE_ABSENT  stop=stop-3" in lines
    assert "14:16  EXCEPTION OPENED  STOP_OVERDUE  stop=stop-4  (watchdog)" in lines
    assert "14:30  EXCEPTION RESOLVED  STOP_OVERDUE  stop=stop-4" in lines
    crossings = [e for e in state.events if e.event_type is EventType.DETENTION_CROSSED]
    overdue = [e for e in state.events if e.event_type is EventType.STOP_OVERDUE]
    assert len(crossings) == len(overdue) == 1
    assert overdue[0].source == "system"
    assert all(e.status is ExceptionStatus.RESOLVED for e in state.exceptions
               if e.exception_type is not EventType.DETENTION_CROSSED)
    assert "18:00  EXCEPTION EXPIRED  DETENTION_CROSSED  stop=stop-2" in lines


def test_replay_is_repeatable_and_script_runs_from_another_directory(tmp_path):
    first, second = [], []
    state = replay_shift.replay(emit=first.append)
    assert replay_shift.replay(emit=second.append) == state
    assert first == second
    result = subprocess.run(
        [sys.executable, str(replay_shift.ROOT / "scripts/replay_shift.py")],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines() == first
    assert result.stderr == ""


def test_fixture_order_is_ingestion_order_and_missing_departure_is_rejected(tmp_path):
    rows = json.loads(replay_shift.FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture = tmp_path / "events.json"
    fixture.write_text(json.dumps(list(reversed(rows))), encoding="utf-8")
    expected, actual = [], []
    replay_shift.replay(emit=expected.append)
    replay_shift.replay(fixture_path=fixture, emit=actual.append)
    assert actual == expected

    fixture.write_text(json.dumps([
        row for row in rows if row["id"] != "clean-departure"
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="REJECTED  gate-arrival"):
        replay_shift.replay(fixture_path=fixture, emit=lambda line: None)
