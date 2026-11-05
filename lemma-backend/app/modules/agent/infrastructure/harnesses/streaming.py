"""Helpers for batching transient assistant token events."""

from __future__ import annotations

import re
import time
from collections.abc import Callable

DEFAULT_TOKEN_CHUNK_WORDS = 6
DEFAULT_TOKEN_CHUNK_CHARS = 240
#: How long after one chunk a `CharStreamBuffer` holds new text for more to
#: join it. Each chunk is a realtime publish, so this also caps the publishes of
#: a stream too slow to fill the size bound in that time: one per interval.
DEFAULT_TOKEN_FLUSH_SECONDS = 0.05


class TextStreamBuffer:
    """Accumulate small text deltas into client-friendly stream chunks."""

    def __init__(
        self,
        *,
        target_words: int = DEFAULT_TOKEN_CHUNK_WORDS,
        max_chars: int = DEFAULT_TOKEN_CHUNK_CHARS,
    ) -> None:
        self.target_words = max(1, target_words)
        self.max_chars = max(1, max_chars)
        self._buffer = ""

    def append(self, text: str, *, force: bool = False) -> list[str]:
        if text:
            self._buffer += text
        return self.drain(force=force)

    def drain(self, *, force: bool = False) -> list[str]:
        chunks: list[str] = []
        while self._buffer:
            split_index = self._split_index(force=force)
            if split_index is None:
                break
            chunks.append(self._buffer[:split_index])
            self._buffer = self._buffer[split_index:]
        return chunks

    def _split_index(self, *, force: bool) -> int | None:
        if force:
            return len(self._buffer)

        if len(self._buffer) >= self.max_chars:
            return self._split_at_char_limit()

        newline_index = self._buffer.find("\n")
        if newline_index != -1 and _word_count(self._buffer[: newline_index + 1]) > 0:
            return newline_index + 1

        if _word_count(self._buffer) < self.target_words:
            return None

        sentence_index = self._sentence_split_index()
        if sentence_index is not None:
            return sentence_index
        return self._split_after_words(self.target_words)

    def _sentence_split_index(self) -> int | None:
        for match in re.finditer(r"[.!?](?:\s+|$)", self._buffer):
            split_index = match.end()
            if _word_count(self._buffer[:split_index]) >= self.target_words:
                return split_index
        return None

    def _split_after_words(self, word_count: int) -> int:
        words = list(re.finditer(r"\S+", self._buffer))
        split_index = words[min(word_count, len(words)) - 1].end()
        while split_index < len(self._buffer) and self._buffer[split_index].isspace():
            split_index += 1
        return split_index

    def _split_at_char_limit(self) -> int:
        split_index = self._buffer.rfind(" ", 0, self.max_chars + 1)
        if split_index <= 0:
            return min(len(self._buffer), self.max_chars)
        while split_index < len(self._buffer) and self._buffer[split_index].isspace():
            split_index += 1
        return split_index


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


class CharStreamBuffer:
    """Accumulate deltas and release them in chunks a reader can watch arrive.

    A chunk goes out when ``max_chars`` have gathered, or when a delta lands
    ``max_wait`` seconds or more after the last chunk went out. The size alone
    was not enough: at tens of tokens a second, fifty characters take a quarter
    of a second to arrive, so a reply appeared in visible jumps and a short one
    appeared whole when its part ended. The wait bounds how far shown text lags
    the model; the size still batches a stream fast enough to outrun it.

    The wait is checked as each delta arrives, not on a timer, so this needs no
    task beside the stream. The cost is that text held when the model pauses
    waits for its next delta or the end of the part -- at most what arrived
    within one ``max_wait`` of the last chunk.
    """

    def __init__(
        self,
        *,
        max_chars: int = 50,
        max_wait: float = DEFAULT_TOKEN_FLUSH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_chars = max(1, max_chars)
        self.max_wait = max_wait
        self._clock = clock
        self._buffer = ""
        # Nothing sent yet counts as long enough ago, so a stream's first text
        # goes straight out: it is the text a reader has waited longest for.
        self._last_emit: float | None = None

    def append(self, text: str, *, force: bool = False) -> list[str]:
        if text:
            self._buffer += text
        return self.drain(force=force)

    def drain(self, *, force: bool = False) -> list[str]:
        now = self._clock()
        chunks: list[str] = []
        while not force and len(self._buffer) >= self.max_chars:
            chunks.append(self._buffer[: self.max_chars])
            self._buffer = self._buffer[self.max_chars :]
        if self._buffer and (force or self._waited_since_last_emit(now)):
            chunks.append(self._buffer)
            self._buffer = ""
        if chunks:
            self._last_emit = now
        return chunks

    def _waited_since_last_emit(self, now: float) -> bool:
        return self._last_emit is None or now - self._last_emit >= self.max_wait
