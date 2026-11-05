"""The wait row a suspending step writes.

Beside the engine rather than inside it for the reason `underlying_work.py`
gives: the engine is at the architecture ratchet's file-size mark, and turning
a `WaitRequest` into a row is a pure mapping with no transaction of its own.
"""

from app.modules.workflow.domain.run import WorkflowRunEntity
from app.modules.workflow.domain.wait import WaitRequest, WorkflowRunWaitEntity


def wait_entity_for(
    run: WorkflowRunEntity, request: WaitRequest
) -> WorkflowRunWaitEntity:
    """The wait row for `run`, suspended on its current node as `request` says."""
    assert run.current_node_id is not None
    payload = dict(request.payload)
    if request.scheduled_at is not None:
        payload.setdefault("scheduled_at", request.scheduled_at.isoformat())
    return WorkflowRunWaitEntity(
        run_id=run.id,
        flow_id=run.flow_id,
        pod_id=run.pod_id,
        node_id=run.current_node_id,
        wait_type=request.wait_type,
        assigned_pod_member_id=request.assigned_pod_member_id,
        external_ref=request.external_ref,
        # Kept in `payload` too: the reconcile sweep still reads it from
        # there, and older rows have only that copy.
        scheduled_at=request.scheduled_at,
        payload=payload,
    )
