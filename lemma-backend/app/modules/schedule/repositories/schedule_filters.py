"""The optional WHERE clauses for a schedule listing.

Separated from the repository because ``list`` already does authorization,
pagination and hydration, and a run of nine ``if``s in the middle of that made
one method carry four unrelated jobs. The next filter should be a line here
rather than another branch there.
"""

from __future__ import annotations

from app.modules.schedule.infrastructure.models.schedule import Schedule


def list_filters(
    *,
    schedule_type,
    is_active,
    pod_id,
    user_id,
    agent_id,
    workflow_id,
    targets_pod_default,
    name,
    cursor,
) -> list:
    """The optional WHERE clauses for a schedule listing.

    A flat list of "supplied → filter" pairs rather than a run of ``if``s in
    the query builder: every one of them is the same decision, and the next
    filter should be a line here rather than another branch inside a method
    that also does authorization, pagination and hydration.

    Falsy-vs-None matters and differs by column. ``is_active`` and
    ``targets_pod_default`` are booleans where ``False`` is a real filter, so
    they test ``is not None``; the rest are ids and names where the empty value
    means "not asked for".
    """
    clauses = []
    if schedule_type:
        clauses.append(Schedule.schedule_type == schedule_type)
    if is_active is not None:
        clauses.append(Schedule.is_active == is_active)
    if pod_id:
        clauses.append(Schedule.pod_id == pod_id)
    if user_id:
        clauses.append(Schedule.user_id == user_id)
    if agent_id:
        clauses.append(Schedule.agent_id == agent_id)
    if workflow_id:
        clauses.append(Schedule.workflow_id == workflow_id)
    if targets_pod_default is not None:
        clauses.append(Schedule.targets_pod_default.is_(targets_pod_default))
    if name:
        clauses.append(Schedule.name == name)
    if cursor is not None:
        clauses.append(Schedule.id > cursor)
    return clauses
