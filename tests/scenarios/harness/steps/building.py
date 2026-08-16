"""Functions, workflows, schedules, apps, bundles, connectors, and usage.

One module rather than seven, because each of these is currently a handful of
verbs. Split it when any one of them grows past a screen — a step module per
noun is the shape to return to, not a rule to follow while it would mean seven
files of four methods.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from harness.drivers.api import items_of

JSON = dict[str, Any]


def function_source(name: str = "increment") -> str:
    """A minimal function the platform will accept.

    The three ``#`` headers are not decoration: the API rejects code without
    ``input_type_name``, ``output_type_name`` and ``function_name``, because the
    runtime builds a typed artifact from them rather than introspecting the
    module. Leaving them out is a 400 that reads like a validation bug until you
    know that.
    """
    return (
        f"#input_type_name: Input\n"
        f"#output_type_name: Output\n"
        f"#function_name: {name}\n"
        f"\n"
        f"from pydantic import BaseModel\n"
        f"\n"
        f"class Input(BaseModel):\n"
        f"    value: int\n"
        f"\n"
        f"class Output(BaseModel):\n"
        f"    value: int\n"
        f"\n"
        f"async def {name}(ctx, data: Input) -> Output:\n"
        f"    return Output(value=data.value + 1)\n"
    )


class BuildingSteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- functions -------------------------------------------------------

    async def creates_a_function(
        self,
        *,
        in_pod: JSON,
        named: str | None = None,
        code: str | None = None,
        kind: str = "API",
    ) -> JSON:
        name = named or f"function_{uuid4().hex[:10]}"
        code = code if code is not None else function_source(name)
        return await self.api.post(
            f"/pods/{in_pod['id']}/functions",
            what=f"{self.label} creating function {name!r}",
            json={"name": name, "code": code, "type": kind},
        )

    async def is_refused_creating_a_function(self, *, in_pod: JSON, named: str) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/functions",
            json={"name": named, "code": function_source(named)},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused creating function "
                f"{named!r}, but it succeeded ({response.status_code})"
            )
        return response.status_code

    async def opens_function(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/functions/{name}")

    async def functions_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/functions"))

    async def deletes_function(self, name: str, *, in_pod: JSON) -> None:
        await self.api.delete(f"/pods/{in_pod['id']}/functions/{name}")

    async def grants_of_function(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/functions/{name}/permissions")

    # --- workflows -------------------------------------------------------

    async def creates_a_workflow(
        self, *, in_pod: JSON, named: str | None = None, mode: str = "GLOBAL"
    ) -> JSON:
        name = named or f"workflow_{uuid4().hex[:10]}"
        return await self.api.post(
            f"/pods/{in_pod['id']}/workflows",
            what=f"{self.label} creating workflow {name!r}",
            json={"name": name, "mode": mode},
        )

    async def opens_workflow(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/workflows/{name}")

    async def workflows_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/workflows"))

    async def deletes_workflow(self, name: str, *, in_pod: JSON) -> None:
        await self.api.delete(f"/pods/{in_pod['id']}/workflows/{name}")

    async def is_refused_creating_a_workflow(self, *, in_pod: JSON, named: str) -> int:
        response = await self.api.call(
            "POST", f"/pods/{in_pod['id']}/workflows", json={"name": named}
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused creating workflow "
                f"{named!r}, but it succeeded ({response.status_code})"
            )
        return response.status_code

    # --- schedules -------------------------------------------------------

    async def creates_a_schedule(
        self,
        *,
        in_pod: JSON,
        named: str | None = None,
        kind: str = "TIME",
        config: JSON | None = None,
        agent: str | None = None,
        workflow: str | None = None,
    ) -> JSON:
        body: JSON = {
            "name": named or f"schedule_{uuid4().hex[:10]}",
            "schedule_type": kind,
            "config": config if config is not None else {"cron": "0 9 * * *"},
        }
        if agent:
            body["agent_name"] = agent
        if workflow:
            body["workflow_name"] = workflow
        return await self.api.post(
            f"/pods/{in_pod['id']}/schedules",
            what=f"{self.label} creating a {kind} schedule",
            json=body,
        )

    async def schedules_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/schedules"))

    async def opens_schedule(self, schedule: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/schedules/{schedule['id']}")

    async def deletes_schedule(self, schedule: JSON, *, in_pod: JSON) -> None:
        await self.api.delete(f"/pods/{in_pod['id']}/schedules/{schedule['id']}")

    async def runs_of_schedule(self, schedule: JSON, *, in_pod: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(f"/pods/{in_pod['id']}/schedules/{schedule['id']}/runs")
        )

    async def is_refused_creating_a_schedule(
        self, *, in_pod: JSON, kind: str = "TIME", config: JSON | None = None
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/schedules",
            json={
                "name": f"bad_{uuid4().hex[:8]}",
                "schedule_type": kind,
                "config": config if config is not None else {},
            },
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused an unusable schedule, "
                f"but it was accepted ({response.status_code})"
            )
        return response.status_code

    # --- apps ------------------------------------------------------------

    async def creates_an_app(self, *, in_pod: JSON, named: str | None = None) -> JSON:
        name = named or f"app_{uuid4().hex[:10]}"
        return await self.api.post(
            f"/pods/{in_pod['id']}/apps",
            what=f"{self.label} creating app {name!r}",
            json={"name": name},
        )

    async def apps_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/apps"))

    # --- resource sharing -------------------------------------------------

    async def access_to(
        self, *, resource_type: str, resource_name: str, in_pod: JSON
    ) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/resources/{resource_type}/{resource_name}/access"
        )

    async def grants(
        self,
        permissions: list[str],
        *,
        on_type: str,
        on_name: str,
        to_member: JSON,
        in_pod: JSON,
    ) -> JSON:
        grantee_id = to_member.get("pod_member_id") or to_member.get("id")
        return await self.api.put(
            f"/pods/{in_pod['id']}/resources/{on_type}/{on_name}"
            f"/access/grantees/POD_MEMBER/{grantee_id}",
            what=f"{self.label} granting {permissions} on {on_name!r}",
            json={"permission_ids": permissions},
        )

    async def revokes_access(
        self, *, on_type: str, on_name: str, from_member: JSON, in_pod: JSON
    ) -> None:
        grantee_id = from_member.get("pod_member_id") or from_member.get("id")
        await self.api.delete(
            f"/pods/{in_pod['id']}/resources/{on_type}/{on_name}"
            f"/access/grantees/POD_MEMBER/{grantee_id}",
            what=f"{self.label} revoking access to {on_name!r}",
        )

    # --- connectors and usage --------------------------------------------

    async def available_connectors(self) -> list[JSON]:
        return items_of(await self.api.get("/connectors"))

    async def connector_status_of(self, organization: JSON) -> JSON:
        return await self.api.get(
            f"/organizations/{organization['id']}/connectors/status"
        )

    async def usage_summary_of(self, organization: JSON) -> JSON:
        return await self.api.get(f"/usage/organizations/{organization['id']}/summary")

    async def own_usage_in(self, organization: JSON) -> JSON:
        return await self.api.get(f"/usage/organizations/{organization['id']}/me")

    async def is_refused_usage_of(self, organization: JSON) -> int:
        response = await self.api.call(
            "GET", f"/usage/organizations/{organization['id']}/summary"
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused usage for an "
                f"organization they do not belong to, but got it "
                f"({response.status_code})"
            )
        return response.status_code

    async def pauses_schedule(self, schedule: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/schedules/{schedule['id']}",
            what=f"{self.label} pausing a schedule",
            json={"is_active": False},
        )

    async def resumes_schedule(self, schedule: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/schedules/{schedule['id']}",
            what=f"{self.label} resuming a schedule",
            json={"is_active": True},
        )
