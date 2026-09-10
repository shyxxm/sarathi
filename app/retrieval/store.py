from collections.abc import Sequence
import math
from typing import Any

from sqlalchemy import Engine, Select, Table, select

from app.contracts.retrieval import Kind, RetrievedChunk, chunk_id
from app.retrieval.embed import Embedder, validate_vectors

TOP_K = 3


def similarity(distance: float) -> float:
    if not math.isfinite(distance):
        raise ValueError("Non-finite cosine distance from retrieval store")
    return min(1.0, max(0.0, 1.0 - distance))


class VectorStore:
    def __init__(self, engine: Engine, embedder: Embedder):
        if engine.dialect.name != "postgresql":
            raise ValueError("Retrieval requires PostgreSQL with pgvector")
        if not embedder.model.strip() or embedder.dimensions < 1:
            raise ValueError("An embedding model and positive dimensions are required")
        self.engine = engine
        self.embedder = embedder

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return validate_vectors(
            self.embedder.embed(texts), count=len(texts), dimensions=self.embedder.dimensions,
        )

    def _rows(
        self, customer_id: str, source_document: str, chunks: Sequence[str],
        vectors: Sequence[Sequence[float]], *, kind: Kind,
    ) -> list[dict[str, Any]]:
        return [dict(
            id=chunk_id(customer_id, source_document, position, kind=kind),
            customer_id=customer_id,
            document_key=chunk_id(customer_id, source_document, 0, kind=kind),
            source_document=source_document, position=position, text=text,
            embedding=vector, embedding_model=self.embedder.model,
            embedding_dimensions=self.embedder.dimensions,
        ) for position, (text, vector) in enumerate(zip(chunks, vectors, strict=True))]

    def _search(self, table: Table, customer_id: str, query_vector: Sequence[float]) -> Select:
        vector = validate_vectors(
            [query_vector], count=1, dimensions=self.embedder.dimensions,
        )[0]
        distance = table.c.embedding.cosine_distance(vector).label("distance")
        # Smallest distance is nearest. Convert to similarity only at the boundary.
        return select(table, distance).where(
            table.c.customer_id == customer_id,
            table.c.embedding_model == self.embedder.model,
            table.c.embedding_dimensions == self.embedder.dimensions,
        ).order_by(distance.asc(), table.c.id).limit(TOP_K)

    def _results(self, statement: Select, *, kind: Kind) -> tuple[RetrievedChunk, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return tuple(RetrievedChunk(
                id=chunk_id(row["customer_id"], row["source_document"], row["position"], kind=kind),
                customer_id=row["customer_id"], source_document=row["source_document"],
                text=row["text"], score=similarity(row["distance"]),
            ) for row in rows)
