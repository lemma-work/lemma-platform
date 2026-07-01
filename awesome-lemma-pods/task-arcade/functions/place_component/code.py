#input_type_name: PlaceComponentInput
#output_type_name: PlaceComponentResult
#function_name: place_component

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


class PlaceComponentInput(BaseModel):
    task_id: str
    component: str
    world_x: int
    world_z: int


class PlaceComponentResult(BaseModel):
    task_id: str
    status: str
    component: str


async def place_component(ctx: FunctionContext, data: PlaceComponentInput) -> PlaceComponentResult:
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
        raise LemmaAPIError(status_code=403, message="Viewers cannot place builds. Clone the pod to start interacting.", code="VIEWER_READONLY")

    task = _row(pod.table("tasks").get(data.task_id))

    if task.get("assignee") != caller_email:
        raise LemmaAPIError(status_code=403, message="You can only place builds for your own tasks.", code="NOT_ASSIGNEE")

    if task.get("status") != "cleared":
        raise LemmaAPIError(status_code=400, message=f"Task must be cleared before placing. Current status: {task.get('status')}.", code="WRONG_STATUS")

    updated = _row(
        pod.table("tasks").update(
            data.task_id,
            {
                "component": data.component,
                "world_x": data.world_x,
                "world_z": data.world_z,
                "status": "under_review",
            },
        )
    )
    return PlaceComponentResult(
        task_id=str(updated["id"]),
        status=updated["status"],
        component=updated["component"],
    )
