#input_type_name: ReviewTaskInput
#output_type_name: ReviewTaskResult
#function_name: review_task

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod, LemmaAPIError


def _row(resp):
    d = resp.to_dict() if hasattr(resp, "to_dict") else resp
    if isinstance(d, dict) and "data" in d:
        return d["data"]
    return d


def _list(resp):
    d = resp.to_dict() if hasattr(resp, "to_dict") else resp
    if isinstance(d, dict) and "items" in d:
        return d["items"]
    return d if isinstance(d, list) else []


class ReviewTaskInput(BaseModel):
    task_id: str
    decision: str  # "approve" | "reject"


class ReviewTaskResult(BaseModel):
    task_id: str
    status: str
    reviewer: str


async def review_task(ctx: FunctionContext, data: ReviewTaskInput) -> ReviewTaskResult:
    pod = Pod.from_env()

    caller_email = ctx.user_email

    members = _list(
        pod.records.list(
            "team_members",
            limit=10,
            filter=[{"field": "email", "op": "eq", "value": caller_email}],
        )
    )
    if not members:
        raise LemmaAPIError(status_code=403, message="You are not a team member.", code="NOT_TEAM_MEMBER")

    role = members[0].get("role", "viewer")

    if role != "manager":
        raise LemmaAPIError(status_code=403, message="Only managers can review and approve/reject builds.", code="MANAGER_ONLY")

    task = _row(pod.table("tasks").get(data.task_id))

    if task.get("status") != "under_review":
        raise LemmaAPIError(status_code=400, message=f"Task is not pending review. Current status: {task.get('status')}.", code="WRONG_STATUS")

    new_status = "established" if data.decision == "approve" else "demolished"
    updated = _row(
        pod.table("tasks").update(
            data.task_id,
            {"status": new_status, "reviewer": caller_email},
        )
    )
    return ReviewTaskResult(
        task_id=str(updated["id"]),
        status=updated["status"],
        reviewer=caller_email,
    )
