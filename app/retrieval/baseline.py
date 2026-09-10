"""How relevant a chunk has to be before it counts as a citation.

SPEC 5 routes to ESCALATE when no SOP is cited, and CLAUDE.md rule 7 forbids a
claim about the driver's pay without one. Both assume "no SOP cited" is a state
the system can actually reach. With a vector store it is not: `search` returns
its top-k whatever the query, so *something* always comes back and the branch
is dead code.

An absolute threshold does not fix it. Cosine similarity has no fixed meaning
across embedding models, and the seeded corpus sits at ~0.60 against questions
it has no answer to — under gemini-embedding-001, a burst tyre retrieves the
Periyar delivery window at 0.61. Pick 0.5 and everything is a citation; pick
0.65 and it has to be re-picked the next time the model changes, by hand, with
nothing to tell you it went stale.

So the floor is measured rather than chosen. At index time each customer's
corpus is queried with probes that are deliberately about nothing it contains,
and the best score any of them achieves is that corpus's noise level. A real
query has to beat it by `CITATION_MARGIN` before its chunk may be spoken from.
Re-measured on every index, so changing the embedding model re-derives it
instead of invalidating it silently.
"""

from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert

from app.retrieval.models import RelevanceBaseline

# Deliberately about nothing any customer SOP covers, while still being the
# kind of thing a driver says. Absurd probes would measure the noise floor of
# absurdity and set the bar too low; these measure it where the real questions
# live. The worst case is what matters, so the baseline is the *highest* score
# any probe reaches, not the average.
NOISE_PROBES: tuple[str, ...] = (
    "the front tyre has burst on the highway",
    "where can I get lunch near the bypass",
    "quarterly amortisation of goodwill in the consolidated accounts",
)

# How far a real query must clear measured noise. Small because the gap it
# lives in is small: across 18 seeded questions the tightest margin over
# baseline was 0.026, so this leaves roughly 0.006 of headroom in the worst
# case. It is a judgement, and the one number here that is not measured —
# but it is a judgement about a *gap*, which survives a model change, and
# scripts/measure_margin.py reprints the distribution it was set from.
#
# No value of this enforces rule 7. Held-out noise scores 0.640 against
# customer-1 while a real question scores 0.635 — the ordering inverts, so
# every cut point is wrong in one direction or the other. This filters the
# obvious cases; SPEC 5.1 has what actually enforces rule 7.
CITATION_MARGIN = 0.02


class MissingBaseline(RuntimeError):
    """Chunks came back for a corpus whose noise level was never measured.

    Refusing is the point. Retrieving with no floor is the behaviour this
    module exists to remove, and defaulting to "cite everything" would restore
    it in exactly the situation nobody is watching — a corpus indexed by some
    path that skipped the measurement.
    """


def save_baseline(
    engine: Engine, customer_id: str, *, model: str, dimensions: int,
    baseline: float, probe_count: int, measured_at: datetime,
) -> None:
    table = RelevanceBaseline.__table__
    values = dict(
        customer_id=customer_id, embedding_model=model, embedding_dimensions=dimensions,
        baseline=baseline, probe_count=probe_count, measured_at=measured_at,
    )
    statement = insert(table).values(**values)
    with engine.begin() as connection:
        connection.execute(statement.on_conflict_do_update(
            index_elements=["customer_id", "embedding_model", "embedding_dimensions"],
            set_={k: statement.excluded[k] for k in ("baseline", "probe_count", "measured_at")},
        ))


def load_baseline(
    engine: Engine, customer_id: str, *, model: str, dimensions: int,
) -> float | None:
    table = RelevanceBaseline.__table__
    with engine.connect() as connection:
        return connection.execute(select(table.c.baseline).where(
            table.c.customer_id == customer_id,
            table.c.embedding_model == model,
            table.c.embedding_dimensions == dimensions,
        )).scalar_one_or_none()
