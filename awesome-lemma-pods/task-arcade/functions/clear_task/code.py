#input_type_name: ClearTaskInput
#output_type_name: ClearTaskResult
#function_name: clear_task

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


class ClearTaskInput(BaseModel):
    task_id: str


class ClearTaskResult(BaseModel):
    task_id: str
    status: str
    points: int


async def clear_task(ctx: FunctionContext, data: ClearTaskInput) -> ClearTaskResult:
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

    if role == "viewer":
        raise LemmaAPIError(status_code=403, message="Viewers cannot clear tasks. Clone the pod to start interacting.", code="VIEWER_READONLY")

    task = _row(pod.table("tasks").get(data.task_id))

    if task.get("assignee") != caller_email:
        raise LemmaAPIError(status_code=403, message="You can only clear tasks assigned to you.", code="NOT_ASSIGNEE")

    if task.get("status") != "assigned":
        raise LemmaAPIError(status_code=400, message=f"Task is already {task.get('status')}, cannot clear.", code="WRONG_STATUS")

    updated = _row(pod.table("tasks").update(data.task_id, {"status": "cleared"}))
    return ClearTaskResult(
        task_id=str(updated["id"]),
        status=updated["status"],
        points=int(updated["points"]),
    )
