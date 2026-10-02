"""What a bundled schedule may bring with it.

A TIME schedule fires on the clock, so it has no event for a filter to judge.
It used to accept `filter_instruction` and ignore it, which means bundles
exported from such a schedule can carry one, and the API now refuses it. Import
drops it with a warning rather than failing a whole bundle over a field that
never did anything.
"""

from __future__ import annotations

from collections.abc import MutableMapping

from app.core.log.log import get_logger

logger = get_logger(__name__)

_FILTER_FIELDS = ("filter_instruction", "filter_output_schema")


def drop_time_filter(
    fields: MutableMapping[str, object], *, warnings: list[str]
) -> None:
    """Take a filter off a TIME schedule's fields, and say so on the import."""
    if fields.get("schedule_type") != "TIME":
        return
    dropped = [key for key in _FILTER_FIELDS if fields.pop(key, None)]
    if not dropped:
        return
    name = str(fields.get("name"))
    warnings.append(
        f"Schedule '{name}' runs on a timer, which has no event to filter, so "
        f"its {' and '.join(dropped)} was not imported."
    )
    logger.warning(
        "pod_bundle.schedule_apply.time_schedule_filter_dropped.degraded",
        schedule_name=name,
        dropped_fields=",".join(dropped),
    )
