"""The `workflows.start` JSONB column, which also carries `run_title`.

`run_title` belongs on the workflow, not on its trigger, and it is a workflow
field everywhere above this file. It is stored inside the `start` column only
because that column is already free-form JSONB and the field shipped without a
schema migration. When a migration next touches `workflows`, give it its own
column and delete this module; nothing outside the infrastructure code that
calls it knows where the field lives.

Stored shape: the trigger's own keys, plus `"run_title": [...]` when one is
set. A workflow with no trigger but a title stores `{"run_title": [...]}` and
no `type`, which is why every read goes through `split_start` — handing that
dict to `WorkflowStart` would fail on the missing `type`.
"""

RUN_TITLE_KEY = "run_title"


def split_start(raw: object) -> tuple[dict[str, object] | None, list[str]]:
    """(the trigger as stored, or None when there is none; the title parts)."""
    if not isinstance(raw, dict):
        return None, []
    start = dict(raw)
    stored = start.pop(RUN_TITLE_KEY, None)
    run_title = (
        [part for part in stored if isinstance(part, str) and part.strip()]
        if isinstance(stored, list)
        else []
    )
    return (start if start.get("type") else None), run_title


def join_start(
    start: dict[str, object] | None, run_title: list[str] | None
) -> dict[str, object] | None:
    """The column value for a trigger and a title. None when there is neither,
    so a workflow without either still stores the NULL it always did."""
    if not run_title:
        return start
    return {**(start or {}), RUN_TITLE_KEY: list(run_title)}
