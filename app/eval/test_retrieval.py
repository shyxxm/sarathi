import json
import math
from types import SimpleNamespace
import warnings

import pytest

from app.contracts.enums import EventType, Intent
from app.contracts.retrieval import RetrievedChunk, RetrievalResult, UnorderedChunks, chunk_id
from app.retrieval.chunks import chunk_markdown
from app.retrieval.embed import LiteLLMEmbedder, validate_vectors
from app.retrieval.retriever import build_query
from app.retrieval.store import similarity


def test_distance_sign_regression_warns_and_similarity_does_not():
    distances = [0.2, 0.4, 0.8]

    def chunks(scores):
        return tuple(RetrievedChunk(
            id=chunk_id("customer-1", "fixture.md", i), customer_id="customer-1",
            source_document="fixture.md", text="Synthetic test passage", score=score,
        ) for i, score in enumerate(scores))

    # Deliberately reproduce the distance-as-score bug: this warning must catch it.
    with pytest.warns(UnorderedChunks):
        RetrievalResult(sop_chunks=chunks(distances))
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnorderedChunks)
        result = RetrievalResult(sop_chunks=chunks([similarity(d) for d in distances]))
    assert [chunk.score for chunk in result.sop_chunks] == pytest.approx([0.8, 0.6, 0.2])
    assert result.retrieval_score == pytest.approx(0.8)


@pytest.mark.parametrize("distance, score", [(0, 1), (0.8, 0.2), (1, 0), (1.5, 0), (2, 0)])
def test_absolute_cosine_scale(distance, score):
    assert similarity(distance) == pytest.approx(score)


@pytest.mark.parametrize("distance", [math.nan, math.inf, -math.inf])
def test_nonfinite_distances_fail_closed(distance):
    with pytest.raises(ValueError):
        similarity(distance)


def test_empty_sop_signal_ignores_precedents():
    precedent = RetrievedChunk(
        id=chunk_id("customer-1", "exception-1", 0, kind="PRECEDENT"),
        customer_id="customer-1", source_document="exception-1", text="Synthetic outcome", score=1,
    )
    result = RetrievalResult(precedents=(precedent,))
    assert result.retrieval_score == 0
    assert result.cited_sop_ids == []
    assert not RetrievalResult()


def test_query_has_all_context_and_deterministic_intents():
    kwargs = dict(event_type=EventType.GATE_CLOSED, customer_id="customer-2",
                  stop_context={"stop_id": "stop-2", "seq": 2, "question": "ഇനി എന്ത്?"})
    query = build_query(intents={Intent.QUESTION, Intent.REPORT}, **kwargs)
    assert query == build_query(intents=[Intent.REPORT, Intent.QUESTION, Intent.REPORT], **kwargs)
    assert json.loads(query) == {
        "intents": ["QUESTION", "REPORT"], "event_type": "GATE_CLOSED",
        "customer_id": "customer-2", "stop_context": kwargs["stop_context"],
    }
    with pytest.raises(ValueError, match="different customer"):
        build_query(intents=[Intent.QUESTION], **(kwargs | {
            "stop_context": {"customer_id": "customer-1"},
        }))
    with pytest.raises(ValueError, match="customer_id"):
        build_query(intents=[Intent.QUESTION], **(kwargs | {"customer_id": ""}))


def test_markdown_chunks_are_bounded_deterministic_and_preserve_words():
    markdown = "# Fixture\n\n" + "first paragraph " * 20 + "\n\n" + "മലയാളം " * 20
    chunks = chunk_markdown(markdown, max_chars=80)
    assert chunks == chunk_markdown(markdown, max_chars=80)
    assert all(0 < len(chunk) <= 80 for chunk in chunks)
    assert " ".join(chunks).split() == markdown.split()
    assert chunk_markdown(" \n\n") == []
    assert chunk_markdown("x" * 200, max_chars=80) == ["x" * 80, "x" * 80, "x" * 40]


@pytest.mark.parametrize("vectors", [[], [[0, 0, 0]], [[1, 0]], [[math.nan, 1, 0]], [[math.inf, 0, 1]]])
def test_invalid_embeddings_rejected(vectors):
    with pytest.raises(ValueError):
        validate_vectors(vectors, count=1, dimensions=3)


def test_litellm_batch_reorders_by_input_index_without_normalising(monkeypatch):
    # No SDK import or network is needed to exercise the adapter.
    import sys

    calls = []

    def embedding(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[
            {"index": 1, "embedding": [3, 4, 0]},
            {"index": 0, "embedding": [1, 0, 0]},
        ])

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(embedding=embedding))
    embedder = LiteLLMEmbedder("fixture/model", 3)
    assert embedder.embed(["one", "two"]) == [[1, 0, 0], [3, 4, 0]]
    assert calls == [{"model": "fixture/model", "input": ["one", "two"]}]
    assert embedder.embed([]) == []


def _chunk(position, score, customer_id="customer-1"):
    return RetrievedChunk(
        id=chunk_id(customer_id, "standing-instructions.md", position),
        customer_id=customer_id, source_document="standing-instructions.md",
        text="Synthetic passage", score=score,
    )


def test_off_topic_retrieval_has_no_context_though_chunks_came_back():
    """The §5 `no SOP cited` branch, which was unreachable before the floor.

    Three chunks come back — a vector store always returns its top-k — and not
    one of them is relevant. Rule 7 turns on this being distinguishable from a
    real answer.
    """
    noise = RetrievalResult(
        sop_chunks=(_chunk(1, 0.61), _chunk(4, 0.60), _chunk(2, 0.59)),
        relevance_floor=0.64,
    )
    assert len(noise.sop_chunks) == 3
    assert noise.cited_sop_chunks == ()
    assert noise.cited_sop_ids == []
    assert noise.retrieval_score == 0.0
    assert noise.has_context is False
    assert not noise


def test_floor_admits_the_relevant_chunk_and_drops_the_rest():
    result = RetrievalResult(
        sop_chunks=(_chunk(2, 0.69), _chunk(1, 0.63), _chunk(5, 0.60)),
        relevance_floor=0.64,
    )
    assert result.cited_sop_ids == ["SOP-CUSTOMER-1-STANDING-INSTRUCTIONS-002"]
    assert result.retrieval_score == pytest.approx(0.69)
    assert result.has_context is True
    assert result


def test_absent_floor_cites_everything_and_is_not_the_safe_default():
    """0.0 means unmeasured, not permissive-by-design. `Retriever` refuses to
    reach this state; the contract still has to represent it honestly."""
    result = RetrievalResult(sop_chunks=(_chunk(1, 0.61), _chunk(4, 0.60)))
    assert result.retrieval_score == pytest.approx(0.61)
    assert len(result.cited_sop_ids) == 2


def test_precedent_below_floor_is_not_context():
    precedent = RetrievedChunk(
        id=chunk_id("customer-1", "exception-1", 0, kind="PRECEDENT"),
        customer_id="customer-1", source_document="exception-1",
        text="Synthetic outcome", score=0.61,
    )
    assert not RetrievalResult(precedents=(precedent,), relevance_floor=0.64).has_context
    assert RetrievalResult(precedents=(precedent,), relevance_floor=0.60).has_context


def test_headings_start_chunks_so_a_rule_stays_with_its_figure():
    """The straddle this replaced: `## Detention` ended one chunk and the rate
    it names began the next, so a detention query cited the heading."""
    markdown = (
        "# Standing Instructions\n\nAccount: customer-1\n\n"
        "## Delivery window\n\nThe booked slot is the slot.\n\n"
        "## Detention\n\nFree waiting time: 30 minutes.\n\n"
        "Beyond that, 250 paise per minute.\n"
    )
    chunks = chunk_markdown(markdown)
    assert len(chunks) == 3
    assert chunks[0].startswith("# Standing Instructions")
    assert chunks[1].startswith("## Delivery window")
    detention = chunks[2]
    assert detention.startswith("## Detention")
    assert "30 minutes" in detention and "250 paise" in detention
    assert " ".join(chunks).split() == markdown.split()
