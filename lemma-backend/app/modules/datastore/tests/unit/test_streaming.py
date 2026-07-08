from __future__ import annotations

import os

import pytest

import app.modules.datastore.infrastructure.streaming as streaming
from app.modules.datastore.infrastructure.streaming import (
    read_file_bytes,
    stream_to_tempfile,
)


async def _aiter(chunks):
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_stream_to_tempfile_writes_all_chunks():
    path = await stream_to_tempfile(_aiter([b"ab", b"cd", b"ef"]), suffix=".pdf")
    try:
        assert path.endswith(".pdf")
        assert read_file_bytes(path) == b"abcdef"
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_stream_to_tempfile_cleans_up_on_stream_error(monkeypatch):
    created: list[str] = []
    real = streaming.tempfile.NamedTemporaryFile

    def _spy(*args, **kwargs):
        handle = real(*args, **kwargs)
        created.append(handle.name)
        return handle

    monkeypatch.setattr(streaming.tempfile, "NamedTemporaryFile", _spy)

    async def _boom():
        yield b"partial"
        raise RuntimeError("stream broke")

    with pytest.raises(RuntimeError, match="stream broke"):
        await stream_to_tempfile(_boom())

    # The partial temp file must not be left behind.
    assert created and not os.path.exists(created[0])
