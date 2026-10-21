"""What a host released before transfers is sent to start a process.

``old_host_params``: such a host ignores ``process.read``'s ``max_bytes``, so
the output it keeps between two reads -- ``output_limit_bytes`` -- is what keeps
every read inside the API's message ceiling. Anything that is not a positive
byte count asks for the bound; nothing is ever asked for past it.
"""

from __future__ import annotations

import pytest

import base64
import json

from app.core.server_limits import WS_MAX_BYTES
from app.modules.agent.services.agent_host_link_ops import (
    OLD_HOST_OUTPUT_LIMIT_BYTES,
    old_host_params,
)


def _limit(params: dict) -> object:
    return old_host_params("process.start", params)["output_limit_bytes"]


def test_a_limit_under_the_bound_is_kept():
    assert _limit({"output_limit_bytes": 4096}) == 4096


def test_a_limit_past_the_bound_is_lowered_to_it():
    assert _limit({"output_limit_bytes": 1024 * 1024}) == OLD_HOST_OUTPUT_LIMIT_BYTES


def test_no_limit_asks_for_the_bound():
    assert _limit({"shell_command": "make"}) == OLD_HOST_OUTPUT_LIMIT_BYTES


# ``True`` is an ``int`` in Python; it is not a byte count.
@pytest.mark.parametrize("asked", [0, -5, True, False, "1024", None, 1.5])
def test_anything_but_a_positive_count_asks_for_the_bound(asked: object):
    assert _limit({"output_limit_bytes": asked}) == OLD_HOST_OUTPUT_LIMIT_BYTES


def test_the_rest_of_the_start_is_sent_as_asked():
    params = {"operation_id": "build", "shell_command": "make", "output_limit_bytes": 1}
    assert old_host_params("process.start", params) == params


def test_another_op_is_sent_untouched():
    params = {"max_bytes": True, "output_limit_bytes": 1024 * 1024}
    assert old_host_params("process.read", params) is params


def test_the_bound_holds_when_every_byte_is_its_own_chunk():
    """The old host answers a read as a JSON object per chunk, data base64'd,
    and bounds what it keeps only in raw bytes. A process that flushes one byte
    at a time is the worst case: its whole ring, read at once, still fits."""
    kept = OLD_HOST_OUTPUT_LIMIT_BYTES
    first = 18_446_744_073_709_551_615 - kept  # the widest sequences there are
    answer = {
        "type": "op_ok",
        "id": "op-18446744073709551615",
        "body": {
            "result": {
                "chunks": [
                    {
                        "sequence": first + n,
                        "stream": "stderr",
                        "data": base64.b64encode(b"x").decode(),
                    }
                    for n in range(kept)
                ],
                "next_sequence": first + kept,
                "truncated_before_sequence": first,
                "state": "running",
                "exit_code": None,
            }
        },
    }
    assert len(json.dumps(answer).encode()) <= WS_MAX_BYTES
