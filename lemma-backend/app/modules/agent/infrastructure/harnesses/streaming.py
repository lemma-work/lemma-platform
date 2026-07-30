"""Helpers for batching transient assistant token events."""

from __future__ import annotations

import re

DEFAULT_TOKEN_CHUNK_WORDS = 6
DEFAULT_TOKEN_CHUNK_CHARS = 240


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
    """Accumulate deltas and emit roughly fixed-size string chunks."""

    def __init__(self, *, max_chars: int = 50) -> None:
        self.max_chars = max(1, max_chars)
        self._buffer = ""

    def append(self, text: str, *, force: bool = False) -> list[str]:
        if text:
            self._buffer += text
        return self.drain(force=force)

    def drain(self, *, force: bool = False) -> list[str]:
        chunks: list[str] = []
        while self._buffer and (force or len(self._buffer) >= self.max_chars):
            split_index = len(self._buffer) if force else self.max_chars
            chunks.append(self._buffer[:split_index])
            self._buffer = self._buffer[split_index:]
        return chunks


class ModelTokenBuffers:
    """Batch model deltas while deferring tool tokens until admission succeeds."""

    def __init__(self, *, emit_tokens: bool, max_chars: int = 50) -> None:
        self.emit_tokens = emit_tokens
        self.buffers = {
            "text": CharStreamBuffer(max_chars=max_chars),
            "thinking": CharStreamBuffer(max_chars=max_chars),
            "tool": CharStreamBuffer(max_chars=max_chars),
        }
        self.deferred_tool_chunks: list[dict[str, str]] = []

    def append(self, kind: str, text: str) -> list[dict[str, str]]:
        if not self.emit_tokens:
            return []
        chunks = [
            {"kind": kind, "data": chunk} for chunk in self.buffers[kind].append(text)
        ]
        return self._route(kind, chunks)

    def drain(
        self,
        kind: str,
        *,
        force: bool = False,
    ) -> list[dict[str, str]]:
        if not self.emit_tokens:
            return []
        chunks = [
            {"kind": kind, "data": chunk}
            for chunk in self.buffers[kind].drain(force=force)
        ]
        return self._route(kind, chunks)

    def drain_all(self, *, force: bool = False) -> list[dict[str, str]]:
        chunks: list[dict[str, str]] = []
        for kind in ("text", "thinking", "tool"):
            chunks.extend(self.drain(kind, force=force))
        return chunks

    def _route(
        self,
        kind: str,
        chunks: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if kind != "tool":
            return chunks
        self.deferred_tool_chunks.extend(chunks)
        return []
