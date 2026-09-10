from sqlalchemy import delete, insert, select

from app.contracts.enums import ExceptionStatus, ResolutionStatus
from app.contracts.retrieval import RetrievedChunk
from app.data.models import OperationalException, Stop
from app.retrieval.chunks import chunk_markdown
from app.retrieval.models import Precedent
from app.retrieval.store import VectorStore


class PrecedentNotApproved(ValueError):
    pass


class PrecedentStore(VectorStore):
    def index_exception(self, exception_id: str) -> list[str]:
        """Read approval, customer and outcome from persisted state, never caller claims."""
        exceptions = OperationalException.__table__
        stops = Stop.__table__
        table = Precedent.__table__
        with self.engine.begin() as connection:
            row = connection.execute(select(exceptions, stops.c.customer_id).select_from(
                exceptions.outerjoin(stops, (exceptions.c.stop_id == stops.c.id)
                                     & (exceptions.c.trip_id == stops.c.trip_id)),
            ).where(exceptions.c.id == exception_id).with_for_update(of=exceptions)).mappings().one_or_none()
            if row is None:
                raise ValueError(f"Unknown exception: {exception_id}")
            if (row["resolution_status"] != ResolutionStatus.APPROVED
                    or row["status"] != ExceptionStatus.RESOLVED
                    or row["resolved_at"] is None):
                raise PrecedentNotApproved("Only resolved, human-APPROVED exceptions become precedent")
            if not row["customer_id"]:
                raise ValueError("Precedent requires an exception with a customer stop")
            if not row["resolution_note"] or not row["resolution_note"].strip():
                raise ValueError("Precedent requires a recorded resolution outcome")
            content = f'{row["exception_type"].value}\n\n{row["resolution_note"]}'
            chunks = chunk_markdown(content)
            rows = self._rows(
                row["customer_id"], f"{exception_id}.md", chunks, self._embed(chunks), kind="PRECEDENT",
            )
            for chunk in rows:
                chunk.update(
                    exception_id=exception_id, resolution_status=ResolutionStatus.APPROVED.value,
                    resolution_note=row["resolution_note"], exception_type=row["exception_type"].value,
                )
            connection.execute(delete(table).where(table.c.exception_id == exception_id))
            connection.execute(insert(table), rows)
        return [chunk["id"] for chunk in rows]

    def search(self, customer_id: str, query_vector: list[float]) -> tuple[RetrievedChunk, ...]:
        table = Precedent.__table__
        exceptions = OperationalException.__table__
        stops = Stop.__table__
        # Revoked approvals, reopened exceptions and edited outcomes disappear immediately.
        statement = self._search(table, customer_id, query_vector).select_from(
            table.join(exceptions, table.c.exception_id == exceptions.c.id).join(
                stops, (exceptions.c.stop_id == stops.c.id) & (exceptions.c.trip_id == stops.c.trip_id),
            ),
        ).where(
            table.c.resolution_status == ResolutionStatus.APPROVED.value,
            exceptions.c.resolution_status == ResolutionStatus.APPROVED,
            exceptions.c.status == ExceptionStatus.RESOLVED,
            exceptions.c.resolved_at.is_not(None),
            stops.c.customer_id == customer_id,
            exceptions.c.resolution_note == table.c.resolution_note,
            exceptions.c.exception_type == table.c.exception_type,
        )
        return self._results(statement, kind="PRECEDENT")
