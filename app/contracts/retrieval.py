"""The boundary between retrieval and everything that reasons about it.

Written before either side exists, because three things have to be true for
SPEC 5 and CLAUDE.md rule 7 to work at all, and none of them can be retrofitted
once ids are in a database and thresholds are tuned to a scale.

1. `score` is comparable across queries. §5 compares it to a fixed threshold,
   so 0.6 has to mean the same thing for every message.
2. `RetrievalResult.retrieval_score` is the one definition of that signal.
   There is no second implementation.
3. `id` survives reindexing. It is what `cited_sop_ids` carries, and a citation
   that stops resolving after a rebuild is worse than no citation.
"""

from pathlib import PurePath
import re
from typing import Literal
import warnings

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

Kind = Literal["SOP", "PRECEDENT"]


class UnorderedChunks(UserWarning):
    """A retriever returned chunks that were not best-first.

    The result is sorted anyway, so nothing downstream breaks — but a retriever
    whose ordering is wrong is usually a retriever whose scoring is wrong, and
    that is worth seeing rather than absorbing silently.
    """


def chunk_id(customer_id: str, source_document: str, position: int, *, kind: Kind = "SOP") -> str:
    """The stable identity of one chunk. Both sides must call this.

    Derived from what the chunk *is* — whose it is, which document, where in
    that document — never from a database row. Reindexing, re-embedding,
    switching vector stores and truncating the table all leave these unchanged;
    a citation written into an exception audit last month still resolves.

    The file extension is dropped: a document's identity is its name, not
    whether it is stored as .md or .txt.

        chunk_id("customer-1", "detention-terms.md", 3)
        -> "SOP-CUSTOMER-1-DETENTION-TERMS-003"

    What does change an id, correctly: re-chunking a document so the text at
    position 3 is different text. That is a different chunk and should not
    inherit the old one's citations.
    """
    if position < 0:
        raise ValueError("Chunk position cannot be negative")
    document = PurePath(source_document).stem
    return "-".join([kind, _slug(customer_id), _slug(document), f"{position:03d}"])


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    if not slug:
        raise ValueError(f"Cannot build a stable id from {value!r}")
    return slug


class RetrievedChunk(BaseModel):
    """One passage, with how well it answers the query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    source_document: str = Field(min_length=1)
    text: str = Field(min_length=1)

    # Cosine similarity between query and chunk, negatives clamped to zero.
    # 1.0 is "says exactly what was asked about", 0.0 is "unrelated".
    #
    # NOT a distance (pgvector's `<=>` is 1 - this, and ordering by it puts the
    # worst match first). NOT a rank. NOT normalised per query — dividing by
    # the top hit would make every query's best chunk score 1.0, including the
    # queries where nothing relevant was found, and §5 would then cite garbage
    # with full confidence on exactly the messages it should escalate.
    score: float = Field(ge=0.0, le=1.0)


class RetrievalResult(BaseModel):
    """What one retrieval call returns. SPEC 6: top-3 of each.

    `precedents` carry resolved exceptions and their outcome, and only ever
    those a human marked APPROVED. That is enforced where the index is written,
    not here — but anything appearing in this list is a claim that a person
    signed off on it. See CLAUDE.md rule 9.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sop_chunks: tuple[RetrievedChunk, ...] = ()
    precedents: tuple[RetrievedChunk, ...] = ()

    @field_validator("sop_chunks", "precedents", mode="after")
    @classmethod
    def _best_first(
        cls, chunks: tuple[RetrievedChunk, ...], info: ValidationInfo,
    ) -> tuple[RetrievedChunk, ...]:
        """Check the retriever ordered them, say so if it did not, sort anyway.

        Downstream code reads `[0]` as the best match and must stay right no
        matter what it was handed. But silently correcting a retriever hides the
        bug: ordering that comes back ascending usually means a distance was
        passed where a similarity was expected, which makes every score in the
        result wrong, not just their order.
        """
        scores = [chunk.score for chunk in chunks]
        if any(earlier < later for earlier, later in zip(scores, scores[1:])):
            warnings.warn(
                f"{info.field_name} arrived out of order ({scores}); sorted "
                "best-first. Check the retriever is returning cosine similarity "
                "and not a distance.",
                UnorderedChunks,
                stacklevel=2,
            )
        return tuple(sorted(chunks, key=lambda chunk: chunk.score, reverse=True))

    @property
    def retrieval_score(self) -> float:
        """The §5 signal. The top SOP chunk's score, 0.0 when nothing was found.

        Precedents are deliberately not counted. A confident precedent match
        with no SOP behind it is exactly the case rule 7 exists for: it means
        the system is about to tell a driver what he is owed on the strength of
        a decision it made earlier, with no standing instruction supporting it.
        That must escalate, not speak.
        """
        return self.sop_chunks[0].score if self.sop_chunks else 0.0

    @property
    def cited_sop_ids(self) -> list[str]:
        """Ids the responder is permitted to cite. Rule 7: a claim about the
        driver's pay or liability needs one of these, and an id that did not
        come from a retrieved chunk is not a citation."""
        return [chunk.id for chunk in self.sop_chunks]

    def __bool__(self) -> bool:
        return bool(self.sop_chunks or self.precedents)
