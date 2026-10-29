"""Field allow-lists for applying a bundled surface/schedule resource.

Single source of truth for "which exported fields actually reach the
create/update call": the backend's apply job (``pod_bundle/infrastructure/
applier.py``) and lemma-cli's direct-import path build their request bodies
from these same constants, so the two importers can't silently drift on which
fields survive an import (e.g. one dropping ``pod_connector`` while the other
keeps it), and what a bundled pod connector may say.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SURFACE_APPLY_FIELDS = frozenset(
    {
        "pod_connector",
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
        "pod_connector",
        "connector_trigger_id",
        "filter_instruction",
        "filter_output_schema",
        "visibility",
    }
)

#: What a bundled pod connector says, and all it says (``connectors/<slug>/
#: <slug>.json``). ``connector`` is the catalog connector (``gmail``), never an
#: installation or an account: which org installation it maps to, and which
#: account a SHARED one runs as, are the importer's choices in their own org.
#: ``visibility`` is written only when it is not the default POD.
POD_CONNECTOR_FIELDS = frozenset(
    {"connector", "mode", "required", "purpose", "visibility"}
)
POD_CONNECTOR_MODES = frozenset({"PER_MEMBER", "SHARED"})
POD_CONNECTOR_VISIBILITIES = frozenset({"POD", "RESTRICTED"})
#: The backend's own slug rule (``connectors.domain.pod_connector``): what reads
#: the same in a URL, a file name and a tool argument.
POD_CONNECTOR_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


@dataclass(frozen=True, slots=True)
class BundleConnector:
    """One pod connector a bundle declares, read and checked."""

    slug: str
    connector: str
    mode: str
    required: bool = False
    purpose: str | None = None
    visibility: str = "POD"

    @property
    def is_shared(self) -> bool:
        return self.mode == "SHARED"


def read_bundle_connector(slug: str, payload: Mapping[str, Any]) -> BundleConnector:
    """A bundled pod connector, or ``ValueError`` saying what is wrong with it.

    Both importers (the backend's plan and the CLI's direct import) read a
    connector through this, so the two cannot disagree about what a valid one
    is. Unknown keys are refused rather than ignored: an id that names one
    organization's installation or account has no meaning in another, and a
    bundle that carries one was not made by an exporter of this format.
    """
    if not POD_CONNECTOR_SLUG_PATTERN.match(slug):
        raise ValueError(
            f"connector '{slug}': a slug is lowercase letters, digits, '-' or "
            "'_', starting with a letter or digit, at most 63 characters"
        )
    unknown = sorted(set(payload) - POD_CONNECTOR_FIELDS)
    if unknown:
        raise ValueError(
            f"connector '{slug}': unexpected field(s) {', '.join(unknown)}. A "
            "bundled connector names the catalog connector and its mode; the "
            "installation and any shared account are chosen on import."
        )
    connector = str(payload.get("connector") or "").strip()
    if not connector:
        raise ValueError(
            f"connector '{slug}': `connector` (the catalog id, e.g. gmail) is required"
        )
    mode = str(payload.get("mode") or "").strip().upper()
    if mode not in POD_CONNECTOR_MODES:
        raise ValueError(
            f"connector '{slug}': `mode` must be PER_MEMBER or SHARED, not "
            f"{payload.get('mode')!r}"
        )
    visibility = str(payload.get("visibility") or "POD").strip().upper()
    if visibility not in POD_CONNECTOR_VISIBILITIES:
        raise ValueError(
            f"connector '{slug}': `visibility` must be POD or RESTRICTED, not "
            f"{payload.get('visibility')!r}"
        )
    required = payload.get("required", False)
    if not isinstance(required, bool):
        raise ValueError(f"connector '{slug}': `required` must be true or false")
    purpose = payload.get("purpose")
    if purpose is not None and not isinstance(purpose, str):
        raise ValueError(f"connector '{slug}': `purpose` must be text")
    return BundleConnector(
        slug=slug,
        connector=connector,
        mode=mode,
        required=required,
        purpose=(purpose or "").strip() or None,
        visibility=visibility,
    )


#: The resource directories whose manifests carry grants, under
#: ``permissions.grants``: what an agent and a function may do on their own.
GRANT_HOLDER_DIRS = ("agents", "functions")

#: Everything a bundled machine says about itself. The disk, the instance and
#: who made it belong to the pod it came from; the grants that let an agent or
#: a function use it travel on that agent or function, by machine name.
MACHINE_APPLY_FIELDS = frozenset(
    {"name", "display_name", "template", "size", "visibility", "run_slots"}
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


def is_past_one_time_schedule(
    payload: Mapping[str, Any], *, now: datetime | None = None
) -> bool:
    """A TIME schedule that runs once (``scheduled_at``, no cron), at a moment
    that is not ahead. It already fired, or was missed, in the pod it came
    from; the server refuses to create one, so an importer leaves it out and
    says so rather than failing part-way. A bare wall-clock time is read in
    the schedule's own ``timezone``, as the server reads it."""
    if str(payload.get("schedule_type") or "").upper() != "TIME":
        return False
    config = payload.get("config")
    if not isinstance(config, Mapping) or config.get("cron"):
        return False
    at = config.get("scheduled_at")
    if not isinstance(at, str):
        return False
    try:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        try:
            zone = ZoneInfo(str(config.get("timezone") or "UTC"))
        except ValueError, ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        moment = moment.replace(tzinfo=zone)
    return moment <= (now or datetime.now(timezone.utc))
