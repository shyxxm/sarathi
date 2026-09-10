"""Index supplied SOPs: python -m scripts.index_sops --customer-id customer-1 path/to/*.md

Indexing also re-measures the customer's relevance floor. The two belong
together: a corpus that changed has a different noise level, and a floor
measured against the old one would license citations against the new one.
"""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

from app.retrieval import CITATION_MARGIN, LiteLLMEmbedder, Retriever, create_schema


def main():
    parser = argparse.ArgumentParser(description="Index supplied customer SOP markdown")
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("documents", nargs="+", type=Path)
    parser.add_argument("--max-chars", type=int, default=1600,
                        help="Chunk size ceiling. A whole SOP under this becomes one chunk.")
    args = parser.parse_args()
    load_dotenv()
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        create_schema(engine)
        retriever = Retriever(engine, LiteLLMEmbedder.from_env())
        for document in args.documents:
            ids = retriever.sops.index_file(
                args.customer_id, document, max_chars=args.max_chars,
            )
            print(f"{document.name}: {', '.join(ids) if ids else 'no chunks'}")
        baseline = retriever.measure_baseline(args.customer_id, datetime.now(timezone.utc))
        print(f"{args.customer_id}: noise baseline {baseline:.3f}, "
              f"citations must clear {baseline + CITATION_MARGIN:.3f}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
