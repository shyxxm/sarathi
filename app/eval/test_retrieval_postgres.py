"""RETRIEVAL_TEST_DATABASE_URL enables real pgvector tests in disposable schemas."""

from datetime import datetime, timezone
import math
import os
from uuid import uuid4
import warnings

import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.contracts.enums import EventType, ExceptionStatus, Intent, ResolutionStatus
from app.contracts.event import OperationalEvent
from app.contracts.exception import OperationalException
from app.contracts.retrieval import RetrievalResult, UnorderedChunks, chunk_id
from app.data.models import OperationalException as ExceptionRow
from app.data.repository import create_schema as create_data_schema, load_seed_data, save
from app.retrieval import (
    CITATION_MARGIN, MissingBaseline, NOISE_PROBES, PrecedentNotApproved, PrecedentStore,
    Retriever, SOPStore, create_schema,
)
from app.retrieval.models import Precedent, SOPChunk

AT = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


class FixtureEmbedder:
    model = "fixture/known-vectors"
    dimensions = 3

    def __init__(self):
        self.calls = []
        self.query_vector = [1, 0, 0]

    def embed(self, texts):
        self.calls.append(list(texts))
        vectors = {"fixture-a": [0.8, 0.6, 0], "fixture-b": [0.6, 0.8, 0],
                   "fixture-c": [0.2, math.sqrt(0.96), 0], "fixture-d": [-1, 0, 0],
                   "fixture-e": [0, 0, 1]}
        # A noise probe is orthogonal to the corpus by construction, which is
        # what one is: the measured baseline comes out at 0.0 and the floor at
        # CITATION_MARGIN, so these tests keep asserting on raw similarity.
        return [
            [0, 0, 1] if any(probe in passage for probe in NOISE_PROBES)
            else vectors.get(passage.rsplit("\n\n", 1)[-1], self.query_vector)
            for passage in texts
        ]


@pytest.fixture
def engine():
    url = os.getenv("RETRIEVAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set RETRIEVAL_TEST_DATABASE_URL to run pgvector integration tests")
    admin = create_engine(url)
    schema = "retrieval_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema},public"},
        execution_options={"schema_translate_map": {None: schema}},
    )
    try:
        create_data_schema(engine)
        load_seed_data(engine)
        create_schema(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def embedder():
    return FixtureEmbedder()


def exception(engine, *, id="exception-1", **changes):
    save(engine, "events", OperationalEvent(
        id="opening-event", source="driver", occurred_at=AT, ingested_at=AT,
        event_type=EventType.GATE_CLOSED, trip_id="trip-1", stop_id="stop-2",
    ))
    values = dict(
        id=id, trip_id="trip-1", stop_id="stop-2", exception_type=EventType.GATE_CLOSED,
        opened_at=AT, opened_by="driver", opening_event_id="opening-event",
        status=ExceptionStatus.RESOLVED, resolution_status=ResolutionStatus.APPROVED,
        resolved_at=AT, resolution_note="Synthetic test outcome",
    ) | changes
    return save(engine, "exceptions", OperationalException(**values))


def measure(retriever, *customer_ids):
    """Indexing re-measures the relevance floor. These tests write through the
    store directly rather than through scripts.index_sops, so they owe the same
    measurement — `retrieve` refuses a corpus that has never been measured."""
    for customer_id in customer_ids:
        retriever.measure_baseline(customer_id, AT)


def retrieve(retriever, customer_id="customer-2"):
    return retriever.retrieve(
        intents=[Intent.REPORT, Intent.QUESTION], event_type=EventType.GATE_CLOSED,
        customer_id=customer_id, stop_context={"seq": 2, "status": "ARRIVED"},
    )


def test_pgvector_top_three_cosine_scores_and_customer_filter(engine, embedder):
    retriever = Retriever(engine, embedder)
    for suffix in "abcd":
        retriever.sops.index_markdown("customer-2", f"{suffix}.md", f"fixture-{suffix}")
    retriever.sops.index_markdown("customer-1", "other.md", "perfect but wrong customer")
    for index in range(4):
        exception(engine, id=f"exception-{index}", resolution_note=f"fixture-{'abcd'[index]}")
        retriever.precedents.index_exception(f"exception-{index}")
    measure(retriever, "customer-1", "customer-2")
    embedder.calls.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnorderedChunks)
        result = retrieve(retriever)
    assert isinstance(result, RetrievalResult)
    assert len(result.sop_chunks) == len(result.precedents) == 3
    assert [chunk.score for chunk in result.sop_chunks] == pytest.approx([0.8, 0.6, 0.2])
    assert [chunk.score for chunk in result.precedents] == pytest.approx([0.8, 0.6, 0.2])
    assert result.retrieval_score == pytest.approx(0.8)
    assert all(chunk.customer_id == "customer-2" for chunk in result.sop_chunks + result.precedents)
    assert len(embedder.calls) == 1 and len(embedder.calls[0]) == 1
    assert retrieve(retriever, "customer-3") == RetrievalResult()


def test_contract_warns_if_store_accidentally_returns_raw_distance(engine, embedder, monkeypatch):
    retriever = Retriever(engine, embedder)
    for suffix in "abc":
        retriever.sops.index_markdown("customer-2", f"{suffix}.md", f"fixture-{suffix}")
    measure(retriever, "customer-2")
    monkeypatch.setattr("app.retrieval.store.similarity", lambda distance: distance)
    with pytest.warns(UnorderedChunks):
        retrieve(retriever)


def test_weak_orthogonal_and_negative_queries_never_become_full_confidence(engine, embedder):
    retriever = Retriever(engine, embedder)
    retriever.sops.index_markdown("customer-2", "weak.md", "fixture-c")
    measure(retriever, "customer-2")
    assert retrieve(retriever).retrieval_score == pytest.approx(0.2)
    embedder.query_vector = [0, 0, 1]
    assert retrieve(retriever).retrieval_score == 0
    embedder.query_vector = [-1, 0, 0]
    assert retrieve(retriever).retrieval_score == 0
    embedder.query_vector = [0, 1, 0]
    assert retrieve(retriever).retrieval_score == pytest.approx(math.sqrt(0.96))


def test_citations_survive_reembedding_table_rebuild_and_extension_change(engine, embedder):
    store = SOPStore(engine, embedder)
    original = store.index_markdown("customer-2", "terms.md", "fixture-a\n\nfixture-b", max_chars=10)
    assert original == [chunk_id("customer-2", "terms.md", i) for i in range(2)]
    assert store.index_markdown("customer-2", "terms.txt", "fixture-a\n\nfixture-b", max_chars=10) == original
    with engine.begin() as connection:
        connection.execute(delete(SOPChunk))
    assert store.index_markdown("customer-2", "terms.md", "fixture-a\n\nfixture-b", max_chars=10) == original
    # Even a database key supplied by a different importer cannot become a citation.
    with engine.begin() as connection:
        connection.execute(update(SOPChunk).where(SOPChunk.id == original[0]).values(id="row-42"))
    assert store.search("customer-2", [1, 0, 0])[0].id == original[0]
    assert store.index_markdown("customer-2", "terms.txt", "fixture-a") == original[:1]
    assert len(store.search("customer-2", [1, 0, 0])) == 1
    store.index_markdown("customer-2", "terms.txt", "")
    assert store.search("customer-2", [1, 0, 0]) == ()


@pytest.mark.parametrize("status", [ResolutionStatus.PENDING, ResolutionStatus.REJECTED])
def test_store_refuses_unapproved_resolution_before_embedding(engine, embedder, status):
    exception(engine, resolution_status=status)
    with pytest.raises(PrecedentNotApproved):
        PrecedentStore(engine, embedder).index_exception("exception-1")
    assert embedder.calls == []
    with engine.connect() as connection:
        assert connection.execute(select(Precedent)).all() == []


@pytest.mark.parametrize("changes", [
    {"status": ExceptionStatus.OPEN}, {"status": ExceptionStatus.EXPIRED}, {"resolved_at": None},
    {"stop_id": None}, {"resolution_note": "  "},
])
def test_approval_alone_is_insufficient(engine, embedder, changes):
    exception(engine, **changes)
    with pytest.raises(ValueError):
        PrecedentStore(engine, embedder).index_exception("exception-1")
    assert embedder.calls == []


@pytest.mark.parametrize("changes", [
    {"resolution_status": ResolutionStatus.REJECTED}, {"resolution_status": ResolutionStatus.PENDING},
    {"status": ExceptionStatus.OPEN}, {"resolution_note": "Changed since approval"},
])
def test_revoked_or_changed_precedents_are_excluded_at_read_time(engine, embedder, changes):
    exception(engine)
    store = PrecedentStore(engine, embedder)
    ids = store.index_exception("exception-1")
    assert ids == [chunk_id("customer-2", "exception-1.md", 0, kind="PRECEDENT")]
    assert store.index_exception("exception-1") == ids
    assert len(store.search("customer-2", [1, 0, 0])) == 1
    assert store.search("customer-1", [1, 0, 0]) == ()
    with engine.begin() as connection:
        connection.execute(update(ExceptionRow).where(ExceptionRow.id == "exception-1").values(**changes))
    assert store.search("customer-2", [1, 0, 0]) == ()


def test_database_rejects_unapproved_precedent_rows(engine, embedder):
    exception(engine)
    store = PrecedentStore(engine, embedder)
    store.index_exception("exception-1")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(update(Precedent).values(resolution_status="PENDING"))


def test_model_mismatch_returns_no_chunks(engine, embedder):
    store = SOPStore(engine, embedder)
    store.index_markdown("customer-2", "fixture.md", "fixture-a")
    other = FixtureEmbedder()
    other.model = "fixture/another-model"
    assert SOPStore(engine, other).search("customer-2", [1, 0, 0]) == ()


def test_embedding_failure_preserves_existing_document(engine, embedder, monkeypatch):
    store = SOPStore(engine, embedder)
    ids = store.index_markdown("customer-2", "fixture.md", "fixture-a")
    monkeypatch.setattr(embedder, "embed", lambda texts: [[0, 0, 0]])
    with pytest.raises(ValueError):
        store.index_markdown("customer-2", "fixture.md", "fixture-b")
    assert [chunk.id for chunk in store.search("customer-2", [1, 0, 0])] == ids


def test_unmeasured_corpus_refuses_and_measured_floor_excludes_a_weak_hit(engine, embedder):
    """End to end: a chunk comes back, is positively similar, and is still not
    context. This is the §5 `no SOP cited` branch against a real pgvector
    search rather than a hand-built RetrievalResult."""
    retriever = Retriever(engine, embedder)
    retriever.sops.index_markdown("customer-2", "weak.md", "fixture-c")

    with pytest.raises(MissingBaseline, match="customer-2"):
        retrieve(retriever)

    assert retriever.measure_baseline("customer-2", AT) == 0.0
    assert retriever.relevance_floor("customer-2") == pytest.approx(CITATION_MARGIN)

    # A query 0.01 away from the stored chunk: real similarity, under the floor.
    chunk, orthogonal = [0.2, math.sqrt(0.96), 0], [math.sqrt(0.96), -0.2, 0]
    weak = 0.01
    embedder.query_vector = [
        weak * chunk[i] + math.sqrt(1 - weak**2) * orthogonal[i] for i in range(3)
    ]
    result = retrieve(retriever)
    assert len(result.sop_chunks) == 1
    assert result.sop_chunks[0].score == pytest.approx(weak, abs=1e-6)
    assert result.cited_sop_ids == []
    assert result.retrieval_score == 0.0
    assert result.has_context is False
