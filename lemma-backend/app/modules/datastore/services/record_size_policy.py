"""Bound how many bytes one record may carry.

Tables are for tabular data. A pod that needs to keep a document keeps it as a
pod file and stores the path, which is what ``FILE_PATH`` is for -- files get
byte ceilings, a search index and converted markdown, and records get status,
summaries and extracted fields. That split is stated in three separate places in
the builder skill and was, until this module, enforced nowhere: every byte
entering a pod as a file passed at least one ceiling, and every byte entering as
a record cell passed none.

What that cost: one production pod put multi-megabyte JSON in a column. Each
write copied the whole row into the ``datastore.events`` Redis stream -- twice
for an update, because the event carries ``previous`` as well -- and that stream
is capped by entry count, not bytes. 50,000 entries of a 3.4 MB row is how Redis
was OOM-killed, and decoding those same entries on the API event loop is how the
loop stalled for 5.4 seconds and Kubernetes restarted the pod. So a cell limit
bounds Redis residency roughly ``maxlen`` times more than it bounds one row.

Two bounds, because either alone leaves a hole: a per-cell limit lets a row with
a hundred just-legal columns through, and a per-row limit alone gives a
confusing error that names no column.

Sizes are measured with an incremental encoder that stops as soon as the budget
is blown, so refusing a 200 MB value costs the limit, not the value. Only
variable-length values are measured -- numbers, booleans and timestamps are
bounded by their own types and counting them would be work with no result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

#: Reasons reported in ``details``, matching the snake_case used by the
#: schema validators for ``enum``, ``not_null`` and ``primary_key``.
CELL_TOO_LARGE = "cell_too_large"
ROW_TOO_LARGE = "row_too_large"

#: Shared by both encoders so a measured size means the same thing everywhere.
_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))


def _measured_size(value: object, budget: int) -> int:
    """Encoded byte size of ``value``, giving up once it passes ``budget``.

    The return value is exact when it is within budget and merely "at least
    budget + 1" when it is not, which is all a refusal needs. Strings and bytes
    are measured directly; everything else is encoded a chunk at a time so a
    pathological value is never fully serialized just to be rejected.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (bytes, bytearray)):
        return len(value)

    total = 0
    try:
        for chunk in _ENCODER.iterencode(value):
            total += len(chunk.encode("utf-8"))
            if total > budget:
                return total
    except TypeError, ValueError:
        # Not JSON-encodable. That is the type validator's refusal to make, not
        # this one's -- report nothing and let it speak.
        return 0
    return total


def size_errors(
    data: Mapping[str, object],
    *,
    cell_max_bytes: int,
    row_max_bytes: int,
) -> tuple[list[str], list[dict[str, object]]]:
    """Refusals for a record that carries too many bytes.

    Returns the ``(messages, details)`` pair both record validators already
    speak, so each can splice these in beside its own findings and raise one
    error naming everything wrong with the write.

    A limit of ``0`` disables that bound, matching ``document_processing``'s
    convention for the same thing.
    """
    errors: list[str] = []
    details: list[dict[str, object]] = []
    row_total = 0

    for key, value in data.items():
        # Measured against the row budget even when the cell budget is smaller,
        # so the running total stays honest for the row check below.
        budget = max(cell_max_bytes, row_max_bytes) if row_max_bytes else cell_max_bytes
        size = _measured_size(value, budget)
        row_total += size
        if cell_max_bytes and size > cell_max_bytes:
            errors.append(
                f"Column '{key}' is {size} bytes, over the {cell_max_bytes} byte "
                "limit for one value. Store the content as a pod file and keep "
                "its path in a FILE_PATH column."
            )
            details.append(
                {
                    "field": key,
                    "reason": CELL_TOO_LARGE,
                    "size_bytes": size,
                    "max_bytes": cell_max_bytes,
                }
            )

    if row_max_bytes and row_total > row_max_bytes:
        errors.append(
            f"The record is {row_total} bytes, over the {row_max_bytes} byte "
            "limit for one row. Store large values as pod files and keep their "
            "paths in FILE_PATH columns."
        )
        details.append(
            {
                "field": "__record__",
                "reason": ROW_TOO_LARGE,
                "size_bytes": row_total,
                "max_bytes": row_max_bytes,
            }
        )

    return errors, details


def exceeds(value: object, limit: int) -> bool:
    """Whether ``value`` encodes to more than ``limit`` bytes.

    ``limit`` of ``0`` means unbounded, matching :func:`size_errors`. Costs the
    limit rather than the value, so asking about a huge structure is cheap.
    """
    if limit <= 0:
        return False
    return _measured_size(value, limit) > limit


def configured_size_errors(
    data: Mapping[str, object],
) -> tuple[list[str], list[dict[str, object]]]:
    """:func:`size_errors` against the deployment's configured limits.

    Both record validators call this rather than reading settings themselves,
    so creates and updates cannot drift apart on what "too large" means.

    On an update ``data`` is the submitted columns, not the resulting row, so
    the row bound covers the write rather than what the row becomes. Reading the
    stored row to check the sum would cost a fetch on every write; the per-cell
    bound is the one that keeps a document out of a column, and the event
    payload is capped independently of both.
    """
    from app.modules.datastore.config import datastore_settings

    return size_errors(
        data,
        cell_max_bytes=datastore_settings.datastore_cell_max_bytes,
        row_max_bytes=datastore_settings.datastore_row_max_bytes,
    )
