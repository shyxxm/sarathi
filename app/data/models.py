from datetime import datetime, time, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Enum as SQLEnum, ForeignKey,
    String, Time, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.contracts.enums import (
    EventType, ExceptionStatus, Language, ReplyMode, ResolutionStatus, StopStatus,
)

SHIFT_TIMEZONE = ZoneInfo("Asia/Kolkata")


class AwareDateTime(TypeDecorator[datetime]):
    """UTC instants; SQLite uses offset-bearing ISO text to retain awareness."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(32))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must be timezone-aware")
        value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="microseconds") if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Database returned a naive timestamp")
        return value.astimezone(timezone.utc)


def enum_column(enum: type[Enum]) -> SQLEnum:
    return SQLEnum(
        enum, values_callable=lambda members: [member.value for member in members],
        native_enum=False, create_constraint=True, validate_strings=True,
    )


class Base(DeclarativeBase):
    type_annotation_map = {datetime: AwareDateTime()}


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    free_detention_minutes: Mapped[int]
    detention_rate_paise_per_min: Mapped[int]
    # A recurring local wall-clock rule, interpreted in SHIFT_TIMEZONE.
    reattempt_cutoff_time: Mapped[time] = mapped_column(Time())
    site_contact: Mapped[str]
    gate_procedure: Mapped[str]
    delivery_window_policy: Mapped[str]


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(primary_key=True)
    registration: Mapped[str] = mapped_column(unique=True)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    preferred_language: Mapped[Language] = mapped_column(enum_column(Language))


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(primary_key=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"))
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id"))
    shift_start: Mapped[datetime]
    shift_end: Mapped[datetime]


class Stop(Base):
    __tablename__ = "stops"
    __table_args__ = (UniqueConstraint("trip_id", "seq"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    seq: Mapped[int]
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    planned_arrival: Mapped[datetime]
    planned_departure: Mapped[datetime]
    service_minutes: Mapped[int]
    window_open: Mapped[datetime]
    window_close: Mapped[datetime]
    status: Mapped[StopStatus] = mapped_column(
        enum_column(StopStatus), default=StopStatus.PENDING, server_default="PENDING",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("source_message_id", name="uq_messages_source_message_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    source_message_id: Mapped[str]
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"))
    occurred_at: Mapped[datetime]
    ingested_at: Mapped[datetime]
    raw_transcript: Mapped[str | None]
    audio_path: Mapped[str | None]
    stt_confidence: Mapped[float | None]


class OperationalEvent(Base):
    __tablename__ = "events"
    __table_args__ = (CheckConstraint("source IN ('driver', 'system')"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    source: Mapped[str]
    occurred_at: Mapped[datetime]
    ingested_at: Mapped[datetime]
    event_type: Mapped[EventType] = mapped_column(enum_column(EventType))
    raw_transcript: Mapped[str | None]
    audio_path: Mapped[str | None]
    language: Mapped[Language | None] = mapped_column(enum_column(Language))
    location_hint: Mapped[str | None]
    driver_claimed_wait_minutes: Mapped[int | None]
    driver_id: Mapped[str | None] = mapped_column(ForeignKey("drivers.id"))
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id"))
    trip_id: Mapped[str | None] = mapped_column(ForeignKey("trips.id"))
    stop_id: Mapped[str | None] = mapped_column(ForeignKey("stops.id"))
    unresolved_fields: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    supersedes_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.source_message_id"))


class OperationalException(Base):
    __tablename__ = "exceptions"
    __table_args__ = (
        CheckConstraint("opened_by IN ('driver', 'system')"),
        CheckConstraint("risk IN ('LOW', 'HIGH')"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    stop_id: Mapped[str | None] = mapped_column(ForeignKey("stops.id"))
    exception_type: Mapped[EventType] = mapped_column(enum_column(EventType))
    opened_at: Mapped[datetime]
    opened_by: Mapped[str]
    opening_event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    status: Mapped[ExceptionStatus] = mapped_column(enum_column(ExceptionStatus))
    resolution_status: Mapped[ResolutionStatus] = mapped_column(
        enum_column(ResolutionStatus), default=ResolutionStatus.PENDING, server_default="PENDING",
    )
    confidence: Mapped[float | None]
    risk: Mapped[str | None]
    reply_mode: Mapped[ReplyMode | None] = mapped_column(enum_column(ReplyMode))
    cost_exposure_paise: Mapped[int] = mapped_column(default=0, server_default="0")
    review_due_at: Mapped[datetime | None]
    driver_informed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    resolved_at: Mapped[datetime | None]
    resolving_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"))
    resolution_note: Mapped[str | None]
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    audit: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, server_default="[]")


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = (CheckConstraint("json_array_length(restated_facts) > 0"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.source_message_id"))
    occurred_at: Mapped[datetime]
    ingested_at: Mapped[datetime]
    mode: Mapped[ReplyMode] = mapped_column(enum_column(ReplyMode))
    language: Mapped[Language] = mapped_column(enum_column(Language))
    text: Mapped[str]
    restated_facts: Mapped[list[str]] = mapped_column(JSON)
    cited_sop_ids: Mapped[list[str]] = mapped_column(JSON)
    audio_path: Mapped[str | None]


class DetentionLedger(Base):
    __tablename__ = "detention_ledger"
    __table_args__ = (
        UniqueConstraint("trip_id", "stop_id", name="uq_detention_ledger_trip_stop"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    stop_id: Mapped[str] = mapped_column(ForeignKey("stops.id"))
    source_event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    arrival_observed_at: Mapped[datetime]
    computed_at: Mapped[datetime]
    free_detention_minutes: Mapped[int]
    billable_minutes: Mapped[int]
    detention_rate_paise_per_min: Mapped[int]
    exposure_paise: Mapped[int]
    driver_claimed_wait_minutes: Mapped[int | None]
