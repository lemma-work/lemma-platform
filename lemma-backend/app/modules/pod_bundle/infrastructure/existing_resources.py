"""Does this resource already exist in the target pod?

Every apply step is an upsert, so each one starts by asking whether the pod
already has the thing the bundle names. The services answer that question three
different ways -- two raise a typed not-found, two return None, one has no
get-by-name at all and needs a filtered list -- so each lookup is wrapped into
the same shape: the resource, or None.

**Only absence is caught here.** Every one of these lookups checks
authorization *after* finding the row, so a catch wide enough to include a
denial answers "no such table" when the truth is "you may not read it" -- and
the applier responds to that by creating a second one. The same goes for a
database being down: retrying an import is recoverable, silently duplicating
every resource in it is not.

Where a service already signals absence by returning None, there is nothing
left to catch and no try/except at all. That is not an oversight; adding one
back would only restore the failure above.

Extracted from `applier.py`, which is well past the 600-line ceiling the
architecture ratchet sets. These are the part of it that is not applying
anything.
"""

from __future__ import annotations

from app.modules.agent.contracts import AgentNotFoundError
from app.modules.datastore.contracts import DatastoreTableNotFoundError


async def _get_table(service, pod_id, name, ctx):
    try:
        return await service.get_table(pod_id, name, ctx)
    except DatastoreTableNotFoundError:
        return None


async def _get_agent(service, pod_id, name, ctx):
    try:
        return await service.get_agent_by_name(pod_id=pod_id, name=name, ctx=ctx)
    except AgentNotFoundError:
        return None


async def _get_function(service, pod_id, name, user_id, ctx):
    # Returns None for a missing function unless asked to raise, which we do
    # not ask for -- so absence arrives as the value, not as an exception.
    return await service.get_function_by_name(
        pod_id, name, user_id, include_code=False, ctx=ctx
    )


async def _get_schedule(service, pod_id, name, ctx):
    # No get-by-name on the schedule service; list with a name filter. An empty
    # page is the absence signal.
    schedules, *_ = await service.list_schedules(pod_id=pod_id, name=name, ctx=ctx)
    return schedules[0] if schedules else None


async def _flow_exists(service, pod_id, name, ctx) -> bool:
    # get_workflow_by_name RETURNS None for a missing flow (it does not raise).
    return await service.get_workflow_by_name(pod_id, name, ctx=ctx) is not None
