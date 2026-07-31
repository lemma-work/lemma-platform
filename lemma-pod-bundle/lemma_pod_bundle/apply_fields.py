"""Field allow-lists for applying a bundled surface/schedule resource.

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

SCHEDULE_APPLY_FIELDS = frozenset(
    {
        "name",
        "schedule_type",
        "config",
        "agent_name",
        "workflow_name",
        "account_id",
        "connector_trigger_id",
        "filter_instruction",
        "filter_output_schema",
        "visibility",
    }
)
