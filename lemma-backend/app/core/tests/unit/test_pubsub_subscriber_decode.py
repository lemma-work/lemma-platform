"""Decoding a stream entry must not own the event loop.

The datastore changes WebSocket reads this stream. When one pod put a document
in a table column, entries reached 3.4MB and were decoded 64 at a time, inline
on the loop -- which the stall sampler caught at 5.4 seconds, past the liveness
threshold, so Kubernetes restarted the API while login was failing.
"""

from __future__ import annotations

import pytest

from app.core.pubsub import subscriber


def _entry(size: int) -> dict:
    return {b"__data__": b"x" * size}


def test_a_large_entry_is_decoded_off_the_loop() -> None:
    assert subscriber._should_offload(_entry(subscriber._OFFLOAD_DECODE_BYTES))


def test_a_small_entry_is_not_worth_the_hand_off() -> None:
    assert not subscriber._should_offload(_entry(16))


def test_an_entry_with_no_binary_envelope_is_not_offloaded() -> None:
    assert not subscriber._should_offload({"plain": "value"})


def test_a_read_batch_cannot_be_large_enough_to_own_the_loop() -> None:
    """A guard on the constant, because the number is the whole fix.

    64 entries per read is what turned a resume into a multi-second stall.
    """
    assert subscriber._READ_BATCH <= 16


@pytest.mark.parametrize("size", [0, 1024, 128 * 1024])
def test_decoding_still_returns_the_payload(size: int) -> None:
    import asyncio

    decoded = asyncio.run(subscriber._decode_entry_async(_entry(size)))

    assert decoded is not None
