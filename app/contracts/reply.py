"""SPEC 3.4. What the responder emits, and what finally gets spoken.

The split is deliberate. `ResponderOutput` is the model boundary — text, the
claims it makes, and its own confidence. `DriverReply` is what the router
assembles once the critic has scored it, and it is the only thing carrying a
`mode`. A prompt that could name its own `ReplyMode` would be deciding its own
routing, and §5 puts that decision in code.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.enums import Language, ReplyMode


class Claim(BaseModel):
    """One assertion about the customer's rules, and the chunk it rests on.

    SPEC 5.1: rule 7 is not enforceable from a retrieval score, because
    similarity cannot tell a chunk that answers the question from a chunk that
    shares its vocabulary. What can tell them apart is asking whether this
    passage actually supports this sentence — and that question can only be
    asked of a claim small enough to check on its own.

    So the responder does not hand back a paragraph with a citation stapled to
    it. It hands back the individual things it is asserting, each already
    attributed. A claim whose `cited_chunk_id` was not in the retrieved set is
    a fabricated citation and fails validation on `DriverReply`; a claim whose
    chunk does not support it is caught by the grounding check in the critic.

    Not every sentence in the reply is a claim. What Sarathi understood goes in
    `restated_facts` — those come from state, not retrieval, and grounding has
    no business checking them against a SOP.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    cited_chunk_id: str = Field(min_length=1)


class ResponderOutput(BaseModel):
    """What the model returns. No `mode`, and no ids it could have invented.

    `extra="forbid"` for the same reason as `InterpreterOutput`: a model that
    tries to route itself, or to set `cited_sop_ids` directly, fails
    validation rather than being believed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: Language
    text: str = Field(min_length=1)
    # No `restated_facts`. Which figures are ours is a question about state,
    # so code writes that list (SPEC 5.2) and the model has nowhere to put one
    # — as the interpreter has nowhere to put a stop_id (SPEC 1.1).
    claims: list[Claim] = Field(default_factory=list)

    # The model's own rating. §5 caps its weight at 0.20 — it is an input to
    # the score, never the score.
    confidence: float = Field(ge=0.0, le=1.0)


class DriverReply(BaseModel):
    """SPEC 3.4. `restated_facts` is not optional and not decorative: it is
    the correction mechanism described in SPEC section 2."""

    model_config = ConfigDict(frozen=True)

    mode: ReplyMode
    language: Language
    text: str
    restated_facts: list[str] = Field(min_length=1)

    # Every claim that survived grounding, and the chunks they rest on.
    # `cited_sop_ids` is derived from the claims rather than carried beside
    # them, so a citation always has the sentence it supports attached.
    claims: list[Claim] = Field(default_factory=list)
    cited_sop_ids: list[str] = Field(default_factory=list)
    audio_path: str | None = None

    @model_validator(mode="after")
    def _citations_belong_to_claims(self) -> "DriverReply":
        """A cited id with no claim behind it is a citation of nothing.

        This is the structural half of rule 7. It cannot tell whether a chunk
        supports a claim — that is the grounding check — but it can guarantee
        the reply never carries a citation that no sentence in it rests on,
        which is how a citation list starts looking like evidence it is not.
        """
        cited = {claim.cited_chunk_id for claim in self.claims}
        if set(self.cited_sop_ids) != cited:
            raise ValueError(
                f"cited_sop_ids {sorted(self.cited_sop_ids)} does not match the "
                f"chunks the claims rest on {sorted(cited)}"
            )
        return self
