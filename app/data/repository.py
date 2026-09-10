"""Plain row dictionaries in and out; every operation owns its transaction."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, time
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import JSON, Engine, Table, Time, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.data.models import AwareDateTime, Base

SEED_DIRECTORY = Path(__file__).with_name("seed")
_JSON_VALUE = TypeAdapter(Any)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError:
        raise ValueError(f"Unknown table: {name}") from None


def _values(table: Table, values: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    data = values.model_dump(mode="python") if isinstance(values, BaseModel) else dict(values)
    unknown = data.keys() - table.columns.keys()
    if unknown:
        raise ValueError(f"Unknown columns for {table.name}: {sorted(unknown)}")
    for name, value in data.items():
        column_type = table.c[name].type
        if isinstance(column_type, AwareDateTime) and isinstance(value, str):
            data[name] = datetime.fromisoformat(value)
        elif isinstance(column_type, Time) and isinstance(value, str):
            data[name] = time.fromisoformat(value)
        elif isinstance(column_type, JSON):
            data[name] = _JSON_VALUE.dump_python(value, mode="json")
    return data


def _insert(session: Session, table: Table):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return postgres_insert(table)
    if dialect == "sqlite":
        return sqlite_insert(table)
    raise ValueError(f"Unsupported database: {dialect}")


def _save(
    session: Session, table: Table, data: dict[str, Any], *, insert_only: bool = False,
) -> dict[str, Any]:
    if "id" not in data:
        raise ValueError("A record id is required")
    keys = ["id"]
    if table.name == "messages":
        keys = ["source_message_id"]
        insert_only = True
    elif table.name == "detention_ledger":
        keys = ["trip_id", "stop_id"]
    if any(data.get(key) is None for key in keys):
        raise ValueError(f"Required record keys: {keys}")

    statement = _insert(session, table).values(**data)
    updates = {
        name: statement.excluded[name]
        for name in data if name != "id" and name not in keys
    }
    if insert_only or not updates:
        statement = statement.on_conflict_do_nothing(index_elements=keys)
    else:
        statement = statement.on_conflict_do_update(index_elements=keys, set_=updates)
    session.execute(statement)
    row = session.execute(select(table).filter_by(**{key: data[key] for key in keys})).mappings().one()
    return deepcopy(dict(row))


def save(engine: Engine, table_name: str, values: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    """Save a complete record; messages retain the first row for their source key.

    Ledger conflicts update the existing stop's row and preserve its id.
    Other records use their id, which callers must keep stable across replay.
    """
    table = _table(table_name)
    data = _values(table, values)
    with Session(engine) as session, session.begin():
        return _save(session, table, data)


def load(engine: Engine, table_name: str, record_id: str) -> dict[str, Any] | None:
    table = _table(table_name)
    with Session(engine) as session:
        row = session.execute(select(table).where(table.c.id == record_id)).mappings().one_or_none()
        return deepcopy(dict(row)) if row is not None else None


def load_all(engine: Engine, table_name: str, **filters: Any) -> list[dict[str, Any]]:
    """Filter by column equality; stops are returned in trip/sequence order."""
    table = _table(table_name)
    values = _values(table, filters)
    ordering = [table.c.trip_id, table.c.seq] if table_name == "stops" else [table.c.id]
    with Session(engine) as session:
        rows = session.execute(select(table).filter_by(**values).order_by(*ordering)).mappings()
        return [deepcopy(dict(row)) for row in rows]


def load_seed_data(engine: Engine, directory: Path = SEED_DIRECTORY) -> None:
    """Insert the demo atomically; repeated loads leave existing records intact."""
    with Session(engine) as session, session.begin():
        for name in ("customers", "vehicles", "drivers", "trips"):
            records = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
            table = _table(name)
            for record in records:
                stops = record.pop("stops", []) if name == "trips" else []
                _save(session, table, _values(table, record), insert_only=True)
                for stop in stops:
                    stop["trip_id"] = record["id"]
                    stop_table = _table("stops")
                    _save(session, stop_table, _values(stop_table, stop), insert_only=True)
