from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from enum import Enum
import json
from typing import Any

from sqlalchemy import Engine

from app.contracts.enums import EventType, Intent
from app.contracts.retrieval import RetrievalResult
from app.retrieval.baseline import (
    CITATION_MARGIN, MissingBaseline, NOISE_PROBES, load_baseline, save_baseline,
)
from app.retrieval.embed import Embedder, validate_vectors
from app.retrieval.precedent_store import PrecedentStore
from app.retrieval.sop_store import SOPStore


def _context_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise TypeError(f"Unsupported stop context value: {type(value).__name__}")


def build_query(
    *, intents: Iterable[Intent], event_type: EventType, customer_id: str,
    stop_context: Mapping[str, Any] | str,
) -> str:
    if not customer_id.strip():
        raise ValueError("A resolved customer_id is required")
    if isinstance(stop_context, Mapping):
        context_customer = stop_context.get("customer_id", customer_id)
        if context_customer != customer_id:
            raise ValueError("Stop context belongs to a different customer")
    intent_values = sorted({Intent(intent).value for intent in intents})
    if not intent_values:
        raise ValueError("At least one intent is required")
    return json.dumps(dict(
        intents=intent_values, event_type=EventType(event_type).value,
        customer_id=customer_id, stop_context=stop_context,
    ), ensure_ascii=False, sort_keys=True, default=_context_value)


class Retriever:
    def __init__(self, engine: Engine, embedder: Embedder):
        self.engine = engine
        self.embedder = embedder
        self.sops = SOPStore(engine, embedder)
        self.precedents = PrecedentStore(engine, embedder)

    def _embed_query(self, query: str) -> list[float]:
        return validate_vectors(
            self.embedder.embed([query]), count=1, dimensions=self.embedder.dimensions,
        )[0]

    def measure_baseline(self, customer_id: str, now: datetime) -> float:
        """Query this customer's SOPs with probes about nothing they cover.

        The best score any probe reaches is what "irrelevant" scores against
        this corpus with this model. Highest rather than mean: the floor has to
        hold against the worst noise, not the typical noise.
        """
        scores = [
            chunks[0].score for probe in NOISE_PROBES
            if (chunks := self.sops.search(customer_id, self._embed_query(build_query(
                intents=[Intent.QUESTION], event_type=EventType.ARRIVED_STOP,
                customer_id=customer_id, stop_context={"situation": probe, "customer_id": customer_id},
            ))))
        ]
        baseline = max(scores, default=0.0)
        save_baseline(
            self.engine, customer_id, model=self.embedder.model,
            dimensions=self.embedder.dimensions, baseline=baseline,
            probe_count=len(NOISE_PROBES), measured_at=now,
        )
        return baseline

    def relevance_floor(self, customer_id: str) -> float | None:
        baseline = load_baseline(
            self.engine, customer_id, model=self.embedder.model,
            dimensions=self.embedder.dimensions,
        )
        return None if baseline is None else min(1.0, baseline + CITATION_MARGIN)

    def retrieve(
        self, *, intents: Iterable[Intent], event_type: EventType, customer_id: str,
        stop_context: Mapping[str, Any] | str,
    ) -> RetrievalResult:
        query = build_query(
            intents=intents, event_type=event_type, customer_id=customer_id, stop_context=stop_context,
        )
        vector = self._embed_query(query)
        sop_chunks = self.sops.search(customer_id, vector)
        precedents = self.precedents.search(customer_id, vector)
        floor = self.relevance_floor(customer_id)
        if floor is None:
            if sop_chunks or precedents:
                raise MissingBaseline(
                    f"No relevance baseline for {customer_id} under "
                    f"{self.embedder.model}/{self.embedder.dimensions}. Index the "
                    f"corpus first — retrieving without a floor cites anything."
                )
            floor = 0.0
        return RetrievalResult(
            sop_chunks=sop_chunks, precedents=precedents, relevance_floor=floor,
        )
