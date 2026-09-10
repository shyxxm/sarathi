from collections.abc import Sequence
from dataclasses import dataclass
import math
import os
from typing import Protocol


class Embedder(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def validate_vectors(
    vectors: Sequence[Sequence[float]], *, count: int, dimensions: int,
) -> list[list[float]]:
    if dimensions < 1 or len(vectors) != count:
        raise ValueError("Embedding count or dimensions do not match")
    result = []
    for vector in vectors:
        values = [float(value) for value in vector]
        if len(values) != dimensions or not all(math.isfinite(value) for value in values):
            raise ValueError("Embedding must have the configured dimensions and finite values")
        if not any(values):
            raise ValueError("Zero embeddings have no cosine similarity")
        result.append(values)
    return result


@dataclass(frozen=True)
class LiteLLMEmbedder:
    model: str
    dimensions: int

    def __post_init__(self):
        if not self.model.strip() or self.dimensions < 1:
            raise ValueError("An embedding model and positive dimensions are required")

    @classmethod
    def from_env(cls) -> "LiteLLMEmbedder":
        from dotenv import load_dotenv

        load_dotenv()
        model = os.getenv("LITELLM_MODEL_EMBEDDING", "")
        dimensions = os.getenv("LITELLM_EMBEDDING_DIMENSIONS", "")
        if not model or not dimensions:
            raise ValueError("Set LITELLM_MODEL_EMBEDDING and LITELLM_EMBEDDING_DIMENSIONS")
        return cls(model=model, dimensions=int(dimensions))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        import litellm

        response = litellm.embedding(model=self.model, input=list(texts))
        data = sorted(response.data, key=lambda item: item["index"])
        if [item["index"] for item in data] != list(range(len(texts))):
            raise ValueError("Embedding response has missing or duplicate input indices")
        return validate_vectors(
            [item["embedding"] for item in data], count=len(texts), dimensions=self.dimensions,
        )
