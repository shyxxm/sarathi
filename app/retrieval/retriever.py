from datetime import datetime

from sqlalchemy import Engine

from app.contracts.retrieval import RetrievalResult
from app.retrieval.baseline import (
    CITATION_MARGIN, MissingBaseline, NOISE_PROBES, load_baseline, save_baseline,
)
from app.retrieval.embed import Embedder, validate_vectors
from app.retrieval.precedent_store import PrecedentStore
from app.retrieval.sop_store import SOPStore


def build_query(*, customer_id: str, situation: str, question: str | None = None) -> str:
    """What gets embedded: the driver's words, and nothing else.

    Until 11 September this was a JSON object carrying the whole resolved stop —
    ids, five ISO timestamps, `seq`, `service_minutes`, the stop status — next
    to what he actually said. None of that is about what he is asking, and the
    cost was not a rounding error: it put four of six real questions on
    customer-2 under that customer's own floor, and one question on customer-3
    *below the measured noise*. SPEC 5.1 has the figures.

    The reason it is this expensive is worth stating, because it is not
    obvious. Boilerplate is not neutral: every constant in here is in the noise
    probe too, so it raises the measured baseline exactly as fast as it raises
    a real question, while pulling every query toward the same point in the
    space and compressing the distance between them. A floor is `baseline +
    margin`, so common text spends headroom and buys no discrimination.
    Measured against the alternatives it earns its keep least of all: adding
    the event type costs ~0.04 of floor and drops a real gate report under
    customer-3's. So nothing is added to every query. Ever.

    `customer_id` selects the corpus in SQL and is deliberately not embedded —
    a customer id is an identifier, not a thing a driver said.

    `question` is the interpreter's plain-English rendering of what he asked.
    That is still his words, and it is the half of a romanised Malayalam
    message that an English SOP can match on.
    """
    if not customer_id.strip():
        raise ValueError("A resolved customer_id is required")
    words = "\n".join(part.strip() for part in (situation, question) if part and part.strip())
    if not words:
        raise ValueError("A query needs the driver's words")
    return words


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
            if (chunks := self.sops.search(customer_id, self._embed_query(
                build_query(customer_id=customer_id, situation=probe))))
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
        self, *, customer_id: str, situation: str, question: str | None = None,
    ) -> RetrievalResult:
        query = build_query(
            customer_id=customer_id, situation=situation, question=question,
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
