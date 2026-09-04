from abc import ABC, abstractmethod
from typing import List


class EmbeddingProviderError(Exception):
    """The embedding provider could not be reached, or refused the request.

    Named rather than a bare ``Exception`` because callers do have to tell it
    apart: the provider being briefly unavailable is worth retrying, and a
    document that cannot be embedded at all is not. The previous
    ``raise Exception(...)`` erased both the type and the distinction, so a
    single upstream 503 read exactly like a permanent failure and took the whole
    datastore file job down with it.
    """


class Embedder(ABC):
    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        pass
