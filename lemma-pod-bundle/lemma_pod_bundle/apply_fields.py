"""Field allow-lists for applying a bundled surface/schedule/decider resource.

Single source of truth for "which exported fields actually reach the
create/update call": the backend's apply job (``pod_bundle/infrastructure/
applier.py``) and lemma-cli's direct-import path build their request bodies
from these same constants, so the two importers can't silently drift on which
fields survive an import (e.g. one dropping ``account_id`` while the other
keeps it).
"""

from __future__ import annotations

SURFACE_APPLY_FIELDS = frozenset(
    {
        "account_id",
        "config",
        "credential_mode",
        "default_agent_name",
        "is_enabled",
    }
)

# The whole of a bundled decider, and the whole of the body that creates one. It
# is also everything the exporter writes: a decider's examples and decisions are
# people's data, so the normalizer keeps these keys rather than stripping others.
DECIDER_APPLY_FIELDS = frozenset({"name", "definition"})

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
