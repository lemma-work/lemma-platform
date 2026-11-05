"""What a schedule's filter is told before its instruction."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue


def filter_instruction_for(
    instruction: str, metadata: Mapping[str, JsonValue] | None
) -> str:
    """The filter's instruction, told first when the row came from outside.

    The filter reads the row before anything else does, and a stranger wrote
    it: the same notice the run gets heads the filter's instruction, so text in
    the row cannot pass itself off as the member's.
    """
    notice = (metadata or {}).get("row_notice")
    if (metadata or {}).get("untrusted_row") and isinstance(notice, str):
        return f"{notice}\n\n{instruction}"
    return instruction
