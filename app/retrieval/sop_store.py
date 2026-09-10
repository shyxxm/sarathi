from pathlib import Path, PurePath

from sqlalchemy import delete, insert, select

from app.contracts.retrieval import RetrievedChunk, chunk_id
from app.data.models import Customer
from app.retrieval.chunks import chunk_markdown
from app.retrieval.models import SOPChunk
from app.retrieval.store import VectorStore


class SOPStore(VectorStore):
    def index_markdown(
        self, customer_id: str, source_document: str, markdown: str, *, max_chars: int = 1600,
    ) -> list[str]:
        """Atomically replace a document, including removal of obsolete tail chunks."""
        source_document = PurePath(source_document).name
        document_key = chunk_id(customer_id, source_document, 0)
        chunks = chunk_markdown(markdown, max_chars=max_chars)
        vectors = self._embed(chunks)
        rows = self._rows(customer_id, source_document, chunks, vectors, kind="SOP")
        table = SOPChunk.__table__
        with self.engine.begin() as connection:
            # Serialise document rebuilds for this customer, even on the first import.
            customer = connection.execute(select(Customer.id).where(
                Customer.id == customer_id,
            ).with_for_update()).scalar_one_or_none()
            if customer is None:
                raise ValueError(f"Unknown customer: {customer_id}")
            connection.execute(delete(table).where(
                table.c.customer_id == customer_id, table.c.document_key == document_key,
            ))
            if rows:
                connection.execute(insert(table), rows)
        return [row["id"] for row in rows]

    def index_file(self, customer_id: str, path: Path, *, max_chars: int = 1600) -> list[str]:
        return self.index_markdown(
            customer_id, path.name, path.read_text(encoding="utf-8"), max_chars=max_chars,
        )

    def search(self, customer_id: str, query_vector: list[float]) -> tuple[RetrievedChunk, ...]:
        return self._results(self._search(SOPChunk.__table__, customer_id, query_vector), kind="SOP")
