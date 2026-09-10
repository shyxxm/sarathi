from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Engine, ForeignKey, Index, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.data.models import Customer, OperationalException


class RetrievalBase(DeclarativeBase):
    pass


class ChunkColumns:
    # The citation is the primary key too; there is no sequence/row identity.
    id: Mapped[str] = mapped_column(primary_key=True)

    @declared_attr
    def customer_id(cls) -> Mapped[str]:
        return mapped_column(ForeignKey(Customer.__table__.c.id))

    document_key: Mapped[str]
    source_document: Mapped[str]
    position: Mapped[int]
    text: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector())
    embedding_model: Mapped[str]
    embedding_dimensions: Mapped[int]


def chunk_constraints():
    return (
        CheckConstraint("position >= 0"),
        CheckConstraint("length(trim(text)) > 0"),
        CheckConstraint("vector_dims(embedding) = embedding_dimensions"),
        CheckConstraint("vector_norm(embedding) > 0"),
    )


class SOPChunk(ChunkColumns, RetrievalBase):
    __tablename__ = "sop_chunks"
    __table_args__ = (
        *chunk_constraints(),
        Index("ix_sop_customer_document", "customer_id", "document_key"),
    )


class RelevanceBaseline(RetrievalBase):
    """The measured noise level of one customer's corpus, per embedding model.

    Keyed by model and dimensions because the number means nothing without
    them: swapping the embedding model changes the whole similarity scale, and
    a baseline measured under the old one would silently license citations
    under the new one. A missing row means this corpus has not been measured,
    which `Retriever` treats as a reason to refuse, not a reason to assume.
    """

    __tablename__ = "relevance_baselines"

    customer_id: Mapped[str] = mapped_column(
        ForeignKey(Customer.__table__.c.id), primary_key=True,
    )
    embedding_model: Mapped[str] = mapped_column(primary_key=True)
    embedding_dimensions: Mapped[int] = mapped_column(primary_key=True)
    baseline: Mapped[float]
    probe_count: Mapped[int]
    measured_at: Mapped[datetime]

    __table_args__ = (
        CheckConstraint("baseline >= 0.0 AND baseline <= 1.0"),
        CheckConstraint("probe_count > 0"),
    )


class Precedent(ChunkColumns, RetrievalBase):
    __tablename__ = "precedents"
    __table_args__ = (
        *chunk_constraints(),
        CheckConstraint("resolution_status = 'APPROVED'"),
        Index("ix_precedent_customer", "customer_id"),
    )

    exception_id: Mapped[str] = mapped_column(ForeignKey(OperationalException.id), index=True)
    resolution_status: Mapped[str] = mapped_column(String())
    resolution_note: Mapped[str]
    exception_type: Mapped[str]


def create_schema(engine: Engine) -> None:
    """Create after app.data.repository.create_schema; exact search at demo scale."""
    if engine.dialect.name != "postgresql":
        raise ValueError("Retrieval requires PostgreSQL with pgvector")
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        RetrievalBase.metadata.create_all(connection)
