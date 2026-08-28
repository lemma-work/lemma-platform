"""Does this resource already exist in the target pod?

Every apply step is an upsert, so each one starts by asking whether the pod
already has the thing the bundle names. The services answer that question five
different ways -- one raises a typed not-found, one returns None, one has no
get-by-name at all and needs a filtered list -- so each lookup is wrapped into
the same shape: the resource, or None.

Extracted from `applier.py`, which is well past the 600-line ceiling the
architecture ratchet sets. These are the part of it that is not applying
anything.
"""

from __future__ import annotations


async def _get_table(service, pod_id, name, ctx):
    # get_table raises DatastoreTableNotFoundError when absent; treat as "create".
    try:
        return await service.get_table(pod_id, name, ctx)
    except Exception:
        return None


async def _get_agent(service, pod_id, name, ctx):
    try:
        return await service.get_agent_by_name(pod_id=pod_id, name=name, ctx=ctx)
    except Exception:
        return None


async def _get_function(service, pod_id, name, user_id, ctx):
    try:
        return await service.get_function_by_name(
            pod_id, name, user_id, include_code=False, ctx=ctx
        )
    except Exception:
        return None


async def _get_schedule(service, pod_id, name, ctx):
    # No get-by-name on the schedule service; list with a name filter.
    try:
        schedules, *_ = await service.list_schedules(pod_id=pod_id, name=name, ctx=ctx)
        return schedules[0] if schedules else None
    except Exception:
        return None


async def _flow_exists(service, pod_id, name, ctx) -> bool:
    # get_workflow_by_name RETURNS None for a missing flow (it does not raise), so a
    # bare try/except would treat "not found" as "exists" and skip the create.
    try:
        return await service.get_workflow_by_name(pod_id, name, ctx=ctx) is not None
    except Exception:
        return False
