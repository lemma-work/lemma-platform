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
from harness.waiting import eventually

JSON = dict[str, Any]

#: A function run has stopped moving when it reaches one of these.
TERMINAL_RUN = {"COMPLETED", "FAILED", "CANCELLED"}

#: A workflow run has stopped moving when it reaches one of these. `WAITING` is
#: terminal *for a scenario's purposes*: the run is parked on a person and will
#: not move until someone answers, so continuing to poll is waiting forever.
TERMINAL_WORKFLOW = {"COMPLETED", "FAILED", "CANCELLED", "WAITING"}


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

    # --- running functions ------------------------------------------------

    async def runs_function(
        self, name: str, *, with_input: JSON, in_pod: JSON, timeout: float = 120.0
    ) -> JSON:
        """Run a function and wait for it to reach a terminal state.

        An API function answers in the request; a JOB function is queued and the
        response is a run to follow. Waiting for both here means a scenario says
        "runs the function" and gets the outcome either way — which is the level
        a person thinks at.
        """
        started = await self.api.post(
            f"/pods/{in_pod['id']}/functions/{name}/runs",
            status=(200, 201, 202),
            what=f"{self.label} running function {name!r}",
            json={"input_data": with_input},
        )
        if str(started.get("status")) in TERMINAL_RUN:
            return started
        return await eventually(
            lambda: self.api.get(
                f"/pods/{in_pod['id']}/functions/{name}/runs/{started['id']}"
            ),
            lambda run: str(run.get("status")) in TERMINAL_RUN,
            describe=f"function {name!r} to finish",
            timeout=timeout,
        )

    async def is_refused_running_function(
        self, name: str, *, with_input: JSON, in_pod: JSON
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/functions/{name}/runs",
            json={"input_data": with_input},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused running {name!r}, "
                f"but it was accepted ({response.status_code})"
            )
        return response.status_code

    async def runs_of_function(self, name: str, *, in_pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{in_pod['id']}/functions/{name}/runs"))

    async def changes_function_code(self, name: str, *, to: str, in_pod: JSON) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/functions/{name}",
            what=f"{self.label} updating function {name!r}",
            json={"code": to},
        )

    # --- running workflows -------------------------------------------------

    async def gives_workflow_a_graph(
        self,
        name: str,
        *,
        nodes: list[JSON],
        edges: list[JSON],
        in_pod: JSON,
        start: JSON | None = None,
    ) -> JSON:
        # `start` describes how the workflow is triggered — `{"type": "MANUAL"}`
        # for one a person runs — not which node comes first. Entry is decided
        # by the graph.
        body: JSON = {"nodes": nodes, "edges": edges, "start": start or {"type": "MANUAL"}}
        return await self.api.put(
            f"/pods/{in_pod['id']}/workflows/{name}/graph",
            what=f"{self.label} giving {name!r} a graph",
            json=body,
        )

    async def is_refused_graph(
        self, name: str, *, nodes: list[JSON], edges: list[JSON], in_pod: JSON
    ) -> int:
        response = await self.api.call(
            "PUT",
            f"/pods/{in_pod['id']}/workflows/{name}/graph",
            json={"nodes": nodes, "edges": edges},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused an unrunnable graph "
                f"for {name!r}, but it was accepted ({response.status_code})"
            )
        return response.status_code

    async def runs_workflow(
        self, name: str, *, in_pod: JSON, timeout: float = 120.0
    ) -> JSON:
        started = await self.api.post(
            f"/pods/{in_pod['id']}/workflows/{name}/runs",
            status=(200, 201, 202),
            what=f"{self.label} running workflow {name!r}",
            json={},
        )
        return await eventually(
            lambda: self.api.get(
                f"/pods/{in_pod['id']}/workflow-runs/{started['id']}"
            ),
            lambda run: str(run.get("status")) in TERMINAL_WORKFLOW,
            describe=f"workflow run {started['id']} to settle",
            timeout=timeout,
        )

    async def workflow_runs_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/workflow-runs"))

    # --- bundles -----------------------------------------------------------

    async def exports_pod(self, pod: JSON, *, timeout: float = 120.0) -> JSON:
        started = await self.api.expect(
            "POST",
            f"/pods/{pod['id']}/bundle/exports",
            status=202,
            what=f"{self.label} exporting {pod.get('name')!r}",
            json={},
        )
        return await eventually(
            lambda: self.api.get(
                f"/pods/{pod['id']}/bundle/exports/{started['export_id']}"
            ),
            lambda job: str(job.get("status")) in {"READY", "FAILED"},
            describe=f"the export of {pod.get('name')!r} to finish",
            timeout=timeout,
        )

    async def downloads_bundle(self, export: JSON) -> bytes:
        """Fetch the exported archive.

        The download URL is signed *and* needs a signed-in caller, so this goes
        through the authenticated client rather than a bare fetch — which is the
        promise being checked as much as it is plumbing.
        """
        url = export.get("download_url") or export.get("url")
        if not url:
            raise AssertionError(f"export carries nothing to download: {export}")
        response = await self.api.call("GET", url)
        if response.status_code != 200:
            raise AssertionError(
                f"{self.label} could not download the bundle: "
                f"{response.status_code}\n  body: {response.text[:500]}"
            )
        return response.content

    async def uploads_bundle(self, archive: bytes, *, into_pod: JSON) -> str:
        staged = await self.api.post(
            f"/pods/{into_pod['id']}/bundle/uploads",
            what=f"{self.label} staging a bundle",
            files={"data": ("bundle.zip", archive, "application/zip")},
        )
        return staged["url"]

    async def plans_import(
        self, url: str, *, into_pod: JSON, timeout: float = 120.0
    ) -> JSON:
        started = await self.api.expect(
            "POST",
            f"/pods/{into_pod['id']}/bundle/imports",
            status=202,
            what=f"{self.label} starting an import",
            json={"kind": "URL", "url": url},
        )
        return await eventually(
            lambda: self.api.get(
                f"/pods/{into_pod['id']}/bundle/imports/{started['import_id']}"
            ),
            lambda job: str(job.get("status"))
            in {"AWAITING_CONFIRMATION", "FAILED", "COMPLETED"},
            describe="the import plan",
            timeout=timeout,
        )

    async def applies_import(
        self,
        plan: JSON,
        *,
        into_pod: JSON,
        variables: JSON | None = None,
        timeout: float = 180.0,
    ) -> JSON:
        import_id = plan.get("import_id") or plan.get("id")
        await self.api.expect(
            "POST",
            f"/pods/{into_pod['id']}/bundle/imports/{import_id}/apply",
            status=202,
            what=f"{self.label} applying an import",
            json={"variables": variables or {}},
        )
        return await eventually(
            lambda: self.api.get(
                f"/pods/{into_pod['id']}/bundle/imports/{import_id}"
            ),
            lambda job: str(job.get("status")) in {"COMPLETED", "FAILED", "CANCELLED"},
            describe="the import to finish applying",
            timeout=timeout,
        )

    # --- connector installs and accounts -----------------------------------

    async def installs_connector(
        self, connector_id: str, *, in_organization: JSON, named: str | None = None
    ) -> JSON:
        return await self.api.post(
            f"/organizations/{in_organization['id']}/connectors/auth-configs",
            what=f"{self.label} installing {connector_id!r}",
            json={
                "connector_id": connector_id,
                "name": named or f"{connector_id}_{uuid4().hex[:8]}",
            },
        )

    async def connects_account(
        self,
        *,
        in_organization: JSON,
        auth_config: JSON,
        credentials: JSON,
        provider_account_id: str | None = None,
    ) -> JSON:
        body: JSON = {
            "auth_config_id": str(auth_config["id"]),
            "credentials": credentials,
        }
        if provider_account_id:
            body["provider_account_id"] = provider_account_id
        return await self.api.post(
            f"/organizations/{in_organization['id']}/connectors/accounts",
            what=f"{self.label} connecting an account",
            json=body,
        )

    async def accounts_in(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(f"/organizations/{organization['id']}/connectors/accounts")
        )

    async def deletes_account(self, account: JSON, *, in_organization: JSON) -> None:
        await self.api.delete(
            f"/organizations/{in_organization['id']}/connectors/accounts/{account['id']}"
        )

    async def installs_http_connector(
        self, *, in_organization: JSON, server_url: str, spec_url: str, named: str | None = None
    ) -> JSON:
        """Install a connector for an API described by its own OpenAPI spec.

        The `http` kind is how anyone connects an internal or bespoke API, and
        it is the one connector kind a scenario can drive end to end without a
        third party — the provider is a server the suite runs itself.
        """
        return await self.api.post(
            f"/organizations/{in_organization['id']}/connectors/auth-configs",
            what=f"{self.label} installing an HTTP connector",
            json={
                "connector_id": "openapi",
                "kind": "http",
                "name": named or f"provider_{uuid4().hex[:8]}",
                "config": {"server_url": server_url, "spec_url": spec_url},
            },
        )

    async def auth_configs_in(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/organizations/{organization['id']}/connectors/auth-configs"
            )
        )

    async def opens_auth_config(self, auth_config: JSON, *, in_organization: JSON) -> JSON:
        return await self.api.get(
            f"/organizations/{in_organization['id']}/connectors/auth-configs/"
            f"{auth_config['name']}"
        )

    async def renames_auth_config(
        self, auth_config: JSON, *, to: str, in_organization: JSON
    ) -> JSON:
        return await self.api.patch(
            f"/organizations/{in_organization['id']}/connectors/auth-configs/"
            f"{auth_config['name']}",
            what=f"{self.label} renaming an installation",
            json={"name": to},
        )

    async def uninstalls_connector(self, auth_config: JSON, *, in_organization: JSON) -> None:
        await self.api.delete(
            f"/organizations/{in_organization['id']}/connectors/auth-configs/"
            f"{auth_config['name']}",
            what=f"{self.label} uninstalling a connector",
        )

    async def operations_of(self, auth_config: JSON, *, in_organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/organizations/{in_organization['id']}/connectors/"
                f"{auth_config['name']}/operations"
            )
        )

    async def operation_detail(
        self, name: str, *, auth_config: JSON, in_organization: JSON
    ) -> JSON:
        return await self.api.get(
            f"/organizations/{in_organization['id']}/connectors/"
            f"{auth_config['name']}/operations/{name}"
        )

    async def runs_operation(
        self,
        name: str,
        *,
        auth_config: JSON,
        in_organization: JSON,
        payload: JSON,
        account: JSON | None = None,
    ) -> JSON:
        body: JSON = {"payload": payload}
        if account is not None:
            body["account_id"] = str(account["id"])
        return await self.api.post(
            f"/organizations/{in_organization['id']}/connectors/"
            f"{auth_config['name']}/operations/{name}/execute",
            what=f"{self.label} running operation {name!r}",
            json=body,
        )

    async def is_refused_running_operation(
        self,
        name: str,
        *,
        auth_config: JSON,
        in_organization: JSON,
        payload: JSON,
        account: JSON | None = None,
    ) -> int:
        body: JSON = {"payload": payload}
        if account is not None:
            body["account_id"] = str(account["id"])
        response = await self.api.call(
            "POST",
            f"/organizations/{in_organization['id']}/connectors/"
            f"{auth_config['name']}/operations/{name}/execute",
            json=body,
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused running {name!r}, "
                f"but it succeeded ({response.status_code})"
            )
        return response.status_code

    async def triggers_of(self, auth_config: JSON, *, in_organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/organizations/{in_organization['id']}/connectors/"
                f"{auth_config['name']}/triggers"
            )
        )
