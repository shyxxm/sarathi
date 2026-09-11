"""Reprint the distribution CITATION_MARGIN was set from.

The margin is the one number in the retrieval path that is chosen rather than
measured, so it needs somewhere to be re-derived when the embedding model or
the corpus changes. Run it after reindexing:

    python -m scripts.measure_margin

It prints, per customer, what deliberately off-topic probes score against that
corpus and what real questions score, and whether the current margin separates
them. Held-out probes are not the ones the baseline was measured from — if one
of those clears the floor, the separation does not generalise and the number to
change is not the margin.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

from app.retrieval import CITATION_MARGIN, LiteLLMEmbedder, NOISE_PROBES, Retriever

HELD_OUT = (
    "my wife is unwell, can I take tomorrow off",
    "diesel price at the pump near Aluva",
    "the clutch is slipping on the ghat road",
)

QUESTIONS = (
    "vehicle waiting at the gate, how much free detention time",
    "consignee absent, can the driver return the same day",
    "arrived after the booked delivery window closed",
    "what does security need at the gate before unloading",
    "nobody is answering at the site, who does the driver call",
    "the wait has gone past the free time, what is the rate",
)


def main():
    load_dotenv()
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        retriever = Retriever(engine, LiteLLMEmbedder.from_env())

        def top(customer_id, situation):
            """Raw top similarity, before the floor — that is what is being calibrated."""
            return retriever.retrieve(
                customer_id=customer_id, situation=situation,
            ).sop_chunks[0].score

        print(f"margin = {CITATION_MARGIN}\n")
        for customer_id in ("customer-1", "customer-2", "customer-3"):
            floor = retriever.relevance_floor(customer_id)
            if floor is None:
                print(f"{customer_id}: not indexed under this model\n")
                continue
            measured = [top(customer_id, probe) for probe in NOISE_PROBES]
            held_out = [top(customer_id, probe) for probe in HELD_OUT]
            asked = [top(customer_id, question) for question in QUESTIONS]
            print(f"{customer_id}   baseline {max(measured):.3f}   floor {floor:.3f}")
            print(f"  measured noise  {[f'{s:.3f}' for s in measured]}")
            print(f"  held-out noise  {[f'{s:.3f}' for s in held_out]}"
                  f"   over floor: {sum(s >= floor for s in held_out)}")
            print(f"  real questions  {[f'{s:.3f}' for s in asked]}"
                  f"   under floor: {sum(s < floor for s in asked)}")
            print(f"  headroom        {min(asked) - max(measured):+.3f} "
                  f"(worst question over measured noise)\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
