"""How relevant a chunk has to be before it counts as a citation.

SPEC 5 routes to ESCALATE when no SOP is cited, and CLAUDE.md rule 7 forbids a
claim about the driver's pay without one. Both assume "no SOP cited" is a state
the system can actually reach. With a vector store it is not: `search` returns
its top-k whatever the query, so *something* always comes back and the branch
is dead code.

An absolute threshold does not fix it. Cosine similarity has no fixed meaning
across embedding models, and the seeded corpus scores well above zero against
questions it has no answer to — under gemini-embedding-001, a burst tyre
retrieves the Periyar delivery window at 0.553. Pick 0.5 and that is a
citation. Pick a number above it and you have hand-fitted this corpus under
this model: it has to be re-picked whenever either changes, by hand, with
nothing to tell you it went stale. The whole seeded noise band has already
moved once — from ~0.60 to ~0.55 — under a change to the query builder, with
the corpus and the model untouched.

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

# How far a real query must clear measured noise. It is a judgement, and the
# one number here that is not measured — but it is a judgement about a *gap*,
# which survives a model change, and scripts/measure_margin.py reprints the
# distribution it was set from.
#
# One value separates all three seeded customers, which was not true until the
# query builder stopped embedding record ids and ISO timestamps beside the
# driver's words. The window across the three is (+0.015, +0.148): below it
# held-out noise gets cited, above it real questions escalate.
#
# It is set near neither end and deliberately above the middle of the risk, not
# the middle of the range. The two edges are not symmetric — under-citing sends
# a driver to a human who can answer him, over-citing tells him what his
# customer's terms say on the strength of a chunk that happens to share his
# vocabulary, and SPEC 5 says which of those to prefer. 0.05 leaves 0.035 of
# clearance over the worst held-out probe and still admits every seeded
# question by 0.098 at the tightest.
#
# What it costs is one real message, and it is the honest one to lose: the
# damaged transcript from SPEC 2, `ivide aar illa pon edukkunil ...`, scores
# 0.594-0.607 and now clears no customer's floor, where at 0.02 it cleared two.
# Its clean counterpart scores 0.651-0.659 and still cites everywhere. Damage
# costs about 0.05 of similarity, which is the gap this margin now spans — so
# a transcript we half-heard no longer states the customer's terms back to the
# driver. It still answers him from his own record and says it will check.
# See SPEC 5.1.
#
# No value of this enforces rule 7. The ordering these numbers describe has
# already inverted once, under nothing more than a wording change in a
# document. That is why this filters the obvious cases and SPEC 5.1's
# grounding check does the enforcing.
CITATION_MARGIN = 0.05


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
