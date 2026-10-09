"""Field allow-lists for applying a bundled surface/schedule resource.

Single source of truth for "which exported fields actually reach the
create/update call": the backend's apply job (``pod_bundle/infrastructure/
applier.py``) and lemma-cli's direct-import path build their request bodies
from these same constants, so the two importers can't silently drift on which
fields survive an import (e.g. one dropping ``account_id`` while the other
keeps it).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SURFACE_APPLY_FIELDS = frozenset(
    {
        "account_id",
        "config",
        "credential_mode",
        "default_agent_name",
        "is_enabled",
    }
)

SCHEDULE_APPLY_FIELDS = frozenset(
    {
        "name",
        "schedule_type",
        "config",
        "instruction",
        "agent_name",
        "workflow_name",
        "account_id",
        "connector_trigger_id",
        "filter_instruction",
        "filter_output_schema",
        "visibility",
    }
)

#: What a TIME schedule may not carry: there is no event for a filter to judge.
TIME_SCHEDULE_FILTER_FIELDS = ("filter_instruction", "filter_output_schema")


def without_time_schedule_filter(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """A schedule payload with any filter left off a TIME schedule.

    Returns the payload and whether a filter was dropped. A time schedule fires
    on the clock, so a filter on one was never asked, and the server now refuses
    a new one; a bundle exported before that may still carry it. Dropping it,
    and saying so, keeps those bundles importable with nothing lost.
    """
    copied = dict(payload)
    if str(copied.get("schedule_type") or "").upper() != "TIME":
        return copied, False
    dropped = any(copied.get(field) for field in TIME_SCHEDULE_FILTER_FIELDS)
    for field in TIME_SCHEDULE_FILTER_FIELDS:
        copied.pop(field, None)
    return copied, dropped
