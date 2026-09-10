from pydantic import BaseModel, Field

from app.contracts.enums import Language, ReplyMode


class DriverReply(BaseModel):
    """SPEC 3.4. `restated_facts` is not optional and not decorative: it is
    the correction mechanism described in SPEC section 2."""

    mode: ReplyMode
    language: Language
    text: str
    restated_facts: list[str] = Field(min_length=1)
    cited_sop_ids: list[str]
    audio_path: str | None = None
