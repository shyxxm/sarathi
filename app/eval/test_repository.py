from datetime import datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine, event, insert, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.schema import CreateTable

from app.contracts.decision import ActionType, Decision, DecisionAction
from app.contracts.enums import EventType, ExceptionStatus, ReplyMode, StopStatus
from app.contracts.event import OperationalEvent
from app.contracts.exception import OperationalException
from app.contracts.reply import DriverReply
from app.data.models import Base, SHIFT_TIMEZONE
from app.data.repository import SEED_DIRECTORY, create_schema, load, load_all, load_seed_data, save

AT = datetime(2026, 9, 10, 10, 42, tzinfo=SHIFT_TIMEZONE)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    create_schema(engine)
    load_seed_data(engine)
    yield engine
    engine.dispose()


def message(**changes):
    return dict(
        id="message-1", source_message_id="voice-note-1", driver_id="driver-1",
        occurred_at=AT - timedelta(minutes=40), ingested_at=AT,
        raw_transcript="Gate closed, waited forty minutes", audio_path=None,
        stt_confidence=0.8,
    ) | changes


def arrival(engine):
    save(engine, "messages", message())
    return save(engine, "events", OperationalEvent(
        id="event-1", source="driver", occurred_at=AT - timedelta(minutes=40),
        ingested_at=AT, event_type=EventType.ARRIVED_STOP, driver_id="driver-1",
        vehicle_id="vehicle-1", trip_id="trip-1", stop_id="stop-2",
        source_message_id="voice-note-1", driver_claimed_wait_minutes=40,
    ))


def test_seed_shape_terms_and_repeat_load(engine):
    assert {name: len(load_all(engine, name)) for name in
            ("customers", "vehicles", "drivers", "trips", "stops")} == {
        "customers": 3, "vehicles": 1, "drivers": 1, "trips": 1, "stops": 5,
    }
    customers = load_all(engine, "customers")
    for field in ("free_detention_minutes", "detention_rate_paise_per_min",
                  "reattempt_cutoff_time", "gate_procedure", "delivery_window_policy"):
        assert len({customer[field] for customer in customers}) == 3
    trip = load(engine, "trips", "trip-1")
    assert trip["shift_start"].astimezone(SHIFT_TIMEZONE).hour == 8
    assert trip["shift_end"].astimezone(SHIFT_TIMEZONE).hour == 18
    stops = load_all(engine, "stops", trip_id="trip-1")
    assert [stop["seq"] for stop in stops] == [1, 2, 3, 4, 5]
    for stop in stops:
        assert stop["window_open"] <= stop["planned_arrival"] < stop["planned_departure"] <= stop["window_close"]
        assert stop["planned_departure"] - stop["planned_arrival"] == timedelta(minutes=stop["service_minutes"])
    save(engine, "stops", stops[0] | {"status": StopStatus.ARRIVED})
    load_seed_data(engine)
    assert len(load_all(engine, "stops")) == 5
    assert load(engine, "stops", "stop-1")["status"] == StopStatus.ARRIVED


def test_message_replay_preserves_original_observation(engine):
    original = save(engine, "messages", message())
    engine.dispose()  # Repeat through a fresh connection, without process-local caches.
    repeated = save(engine, "messages", message(
        id="message-retry", ingested_at=AT + timedelta(hours=1), raw_transcript="retry",
    ))
    assert repeated == original
    assert len(load_all(engine, "messages", source_message_id="voice-note-1")) == 1
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert(Base.metadata.tables["messages"]).values(**message(id="duplicate")))


def test_event_and_exception_contract_round_trip(engine):
    recorded = arrival(engine)
    assert OperationalEvent.model_validate(recorded).driver_claimed_wait_minutes == 40
    exception = OperationalException(
        id="exception-1", trip_id="trip-1", stop_id=None,
        exception_type=EventType.DRIVER_SILENT, opened_at=AT, opened_by="system",
        opening_event_id="event-1", status=ExceptionStatus.OPEN,
    )
    row = save(engine, "exceptions", exception)
    assert OperationalException.model_validate(row) == exception
    assert row["confidence"] is row["risk"] is row["reply_mode"] is None
    assert row["driver_informed"] is False
    with engine.connect() as connection:
        assert connection.execute(select(Base.metadata.tables["exceptions"].c.id).where(
            Base.metadata.tables["exceptions"].c.decision.is_(None),
        )).scalar_one() == "exception-1"
    exception.decision = Decision(
        actions=[DecisionAction(type=ActionType.NO_ACTION)], rationale="Awaiting review",
        review_in_minutes=20, cited_sop_ids=["SOP-1"],
    )
    exception.confidence = 0.65
    exception.risk = "HIGH"
    exception.reply_mode = ReplyMode.SPEAK_HEDGED
    exception.review_due_at = AT + timedelta(minutes=20)
    exception.driver_informed = True
    exception.audit = [{"action": "review", "at": AT.isoformat()}]
    save(engine, "exceptions", exception)
    assert OperationalException.model_validate(load(engine, "exceptions", exception.id)) == exception
    row["audit"].append({"detached": True})
    assert load(engine, "exceptions", exception.id)["audit"] == exception.audit


def test_schema_matches_exception_contract_and_postgres_timestamps(engine):
    table = Base.metadata.tables["exceptions"]
    assert set(table.columns.keys()) == set(OperationalException.model_fields)
    nullable = {"stop_id", "confidence", "risk", "reply_mode", "review_due_at",
                "resolved_at", "resolving_event_id", "resolution_note", "decision"}
    assert {column.name for column in table.c if column.nullable} == nullable
    assert {"source_message_id"} in [set(item["column_names"]) for item in
                                    inspect(engine).get_unique_constraints("messages")]
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for column in table.c:
            if column.name.endswith("_at") or column.name in {
                "shift_start", "shift_end", "planned_arrival", "planned_departure",
                "window_open", "window_close",
            }:
                assert f"{column.name} TIMESTAMP WITH TIME ZONE" in ddl


@pytest.mark.parametrize("column", ["occurred_at", "ingested_at"])
def test_naive_message_timestamp_rejected_and_rolled_back(engine, column):
    with pytest.raises(StatementError, match="timezone-aware"):
        save(engine, "messages", message(**{column: AT.replace(tzinfo=None)}))
    assert load_all(engine, "messages") == []
    row = save(engine, "messages", message())
    assert row["occurred_at"] == AT - timedelta(minutes=40)
    assert row["ingested_at"] == AT


def test_ledger_upserts_one_snapshot_per_stop(engine):
    arrival(engine)
    ledger = dict(
        id="ledger-1", trip_id="trip-1", stop_id="stop-2", source_event_id="event-1",
        arrival_observed_at=AT, computed_at=AT + timedelta(minutes=61),
        free_detention_minutes=60, billable_minutes=1,
        detention_rate_paise_per_min=150, exposure_paise=150, driver_claimed_wait_minutes=40,
    )
    assert set(Base.metadata.tables["detention_ledger"].columns.keys()) == set(ledger)
    save(engine, "detention_ledger", ledger)
    customer = load(engine, "customers", "customer-2")
    save(engine, "customers", customer | {"free_detention_minutes": 90, "detention_rate_paise_per_min": 900})
    assert load(engine, "detention_ledger", "ledger-1") == ledger
    update = ledger | {"id": "ledger-tick-2", "computed_at": AT + timedelta(minutes=62),
                       "billable_minutes": 2, "exposure_paise": 300}
    row = save(engine, "detention_ledger", update)
    assert row == update | {"id": "ledger-1"}
    assert save(engine, "detention_ledger", update) == row
    assert load_all(engine, "detention_ledger") == [row]
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert(Base.metadata.tables["detention_ledger"]).values(**update))


def test_reply_round_trip_and_required_readback(engine):
    arrival(engine)
    reply = DriverReply(
        mode=ReplyMode.SPEAK, language="ml", text="രണ്ടാമത്തെ സ്റ്റോപ്പിൽ എത്തിയതായി രേഖപ്പെടുത്തി.",
        restated_facts=["Arrived at stop two at 10:42"], cited_sop_ids=["SOP-2"],
    )
    values = reply.model_dump() | dict(
        id="reply-1", driver_id="driver-1", source_message_id="voice-note-1",
        occurred_at=AT, ingested_at=AT,
    )
    assert DriverReply.model_validate(save(engine, "replies", values)) == reply
    with pytest.raises(IntegrityError):
        save(engine, "replies", values | {"restated_facts": []})
    assert load(engine, "replies", "reply-1")["restated_facts"] == reply.restated_facts


def test_seed_import_is_atomic(engine, tmp_path):
    directory = tmp_path / "broken-seed"
    directory.mkdir()
    for name in ("customers", "vehicles", "drivers", "trips"):
        records = json.loads((SEED_DIRECTORY / f"{name}.json").read_text())
        if name == "customers":
            records.append(records[0] | {"id": "new-customer"})
        if name == "trips":
            records[0]["stops"].append(records[0]["stops"][0] | {
                "id": "bad-stop", "seq": 6, "customer_id": "missing-customer",
            })
        (directory / f"{name}.json").write_text(json.dumps(records))
    with pytest.raises(IntegrityError):
        load_seed_data(engine, directory)
    assert load(engine, "customers", "new-customer") is None
    assert len(load_all(engine, "stops")) == 5
