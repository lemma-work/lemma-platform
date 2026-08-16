"""Automating work → defining functions and workflows.

Proves promises in
[docs/product/journeys/automating-work.md](../../../../docs/product/journeys/automating-work.md).
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Automating work"), capability("Write a function")]

#: Creating a function is not a metadata write: the API provisions a sandbox
#: and extracts the declared input/output schemas by loading the code in it.
#: So every scenario that creates one needs the workspace images built, and
#: sits in the sandbox lane rather than the fast one.
needs_sandbox = pytest.mark.sandbox


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@needs_sandbox
@scenario("A person creates a function and it becomes available in the pod")
@proves("PS-FUNC-001")
@covers("function.create", "function.get", "function.list", "function.created")
async def test_a_function_is_created(pod):
    alice, the_pod = pod

    function = await alice.creates_a_function(in_pod=the_pod)

    reopened = await alice.opens_function(function["name"], in_pod=the_pod)
    assert reopened["name"] == function["name"]
    assert function["name"] in {f["name"] for f in await alice.functions_in(the_pod)}


@needs_sandbox
@scenario("A function name already used in the pod is refused")
@proves("PS-FUNC-001")
@covers("function.create")
async def test_a_duplicate_function_name_is_refused(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)

    await alice.is_refused_creating_a_function(in_pod=the_pod, named=function["name"])


@needs_sandbox
@scenario("A person can ask what a function is allowed to reach")
@proves("PS-FUNC-003")
@covers("function.permissions.get")
async def test_a_functions_grants_are_readable(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)

    grants = await alice.grants_of_function(function["name"], in_pod=the_pod)

    assert "grants" in grants, grants


@needs_sandbox
@scenario("Deleting a function stops it being runnable")
@proves("PS-FUNC-004")
@covers("function.delete", "function.list")
async def test_deleting_a_function_removes_it(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)

    await alice.deletes_function(function["name"], in_pod=the_pod)

    assert function["name"] not in {f["name"] for f in await alice.functions_in(the_pod)}


@scenario("Someone outside the pod cannot create a function in it")
@proves("PS-FUNC-003")
@covers("function.create")
async def test_an_outsider_cannot_create_a_function(world, pod):
    alice, the_pod = pod
    outsider = await world.new_person("outsider")

    await outsider.is_refused_creating_a_function(in_pod=the_pod, named="trespass")


class TestWorkflows:
    pytestmark = capability("Build a workflow")

    @scenario("A person creates a workflow and it becomes available in the pod")
    @proves("PS-FLOW-001")
    @covers("workflow.create", "workflow.get", "workflow.list", "workflow.created")
    async def test_a_workflow_is_created(self, pod):
        alice, the_pod = pod

        workflow = await alice.creates_a_workflow(in_pod=the_pod)

        reopened = await alice.opens_workflow(workflow["name"], in_pod=the_pod)
        assert reopened["name"] == workflow["name"]
        listed = {w["name"] for w in await alice.workflows_in(the_pod)}
        assert workflow["name"] in listed

    @scenario("A workflow name already used in the pod is refused")
    @proves("PS-FLOW-001")
    @covers("workflow.create")
    async def test_a_duplicate_workflow_name_is_refused(self, pod):
        alice, the_pod = pod
        workflow = await alice.creates_a_workflow(in_pod=the_pod)

        await alice.is_refused_creating_a_workflow(
            in_pod=the_pod, named=workflow["name"]
        )

    @pytest.mark.xfail(
        reason="DEV-FLOW-001: both visualize endpoints 500 on Starlette 1.3.1",
        strict=True,
    )
    @scenario("A person can see the shape of a workflow without running it")
    @proves("PS-FLOW-002")
    @covers("workflow.visualize")
    async def test_a_workflow_can_be_visualised(self, pod):
        alice, the_pod = pod
        workflow = await alice.creates_a_workflow(in_pod=the_pod)

        shape = await alice.api.get(
            f"/pods/{the_pod['id']}/workflows/{workflow['name']}/visualize"
        )

        assert shape is not None

    @scenario("Deleting a workflow removes it from the pod")
    @proves("PS-FLOW-001")
    @covers("workflow.delete", "workflow.list")
    async def test_deleting_a_workflow_removes_it(self, pod):
        alice, the_pod = pod
        workflow = await alice.creates_a_workflow(in_pod=the_pod)

        await alice.deletes_workflow(workflow["name"], in_pod=the_pod)

        listed = {w["name"] for w in await alice.workflows_in(the_pod)}
        assert workflow["name"] not in listed

    @scenario("Someone outside the pod cannot create a workflow in it")
    @proves("PS-FLOW-014")
    @covers("workflow.create")
    async def test_an_outsider_cannot_create_a_workflow(self, world, pod):
        alice, the_pod = pod
        outsider = await world.new_person("outsider")

        await outsider.is_refused_creating_a_workflow(in_pod=the_pod, named="trespass")
