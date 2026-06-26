#input_type_name: AssignTaskInput
#output_type_name: AssignTaskResult
#function_name: assign_task

from pydantic import BaseModel, field_validator
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


VALID_POINTS = {15, 30, 45, 60}


class AssignTaskInput(BaseModel):
    title: str
    assignee_email: str
    points: int
    sprint_id: str
    source: str = "slack"
    due: str = ""

    @field_validator("points")
    @classmethod
    def validate_points(cls, v: int) -> int:
        if v not in VALID_POINTS:
            raise ValueError(f"points must be one of {VALID_POINTS}, got {v}")
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in ("slack", "email", "telegram"):
            raise ValueError("source must be slack, email, or telegram")
        return v


class AssignTaskResult(BaseModel):
    task_id: str
    status: str


async def assign_task(ctx: FunctionContext, data: AssignTaskInput) -> AssignTaskResult:
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
        raise LemmaAPIError(status_code=403, message="You are not a team member. Ask a manager to add you.", code="NOT_TEAM_MEMBER")

    role = members[0].get("role", "viewer")

    if role == "viewer":
        raise LemmaAPIError(status_code=403, message="Viewers cannot assign tasks. Clone the pod to start interacting.", code="VIEWER_READONLY")

    if role == "member" and data.assignee_email != caller_email:
        raise LemmaAPIError(status_code=403, message="Members can only assign tasks to themselves. Only managers can assign to others.", code="MEMBER_SELF_ASSIGN_ONLY")

    row = _row(
        pod.table("tasks").create(
            {
                "title": data.title,
                "assignee": data.assignee_email,
                "assigner": caller_email,
                "points": data.points,
                "source": data.source,
                "sprint_id": data.sprint_id,
                "status": "assigned",
                "due": data.due,
            }
        )
    )
    return AssignTaskResult(task_id=str(row["id"]), status=row["status"])
