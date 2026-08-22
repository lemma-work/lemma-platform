"""Automating work → changing a workflow, and steering its runs."""

from __future__ import annotations

import pytest


from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Automating work"), capability("Build a workflow")]


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A person changes a workflow's description without touching its graph")
@proves("PS-FLOW-001")
@covers("workflow.update", "workflow.get")
async def test_a_workflow_can_be_changed(pod):
    alice, the_pod = pod
    workflow = await alice.creates_a_workflow(in_pod=the_pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[{"id": "done", "type": "END"}], edges=[], in_pod=the_pod,
    )

    await alice.changes_workflow(
        workflow["name"], in_pod=the_pod, description="Runs the nightly close"
    )

    reopened = await alice.opens_workflow(workflow["name"], in_pod=the_pod)
    assert reopened["description"] == "Runs the nightly close", reopened
    assert reopened["nodes"], "changing the description must not clear the graph"


@scenario("A person sees the runs of one workflow")
@proves("PS-FLOW-010")
@covers("workflow.run.list", "workflow.run.create", "workflow_run.completed")
async def test_a_workflows_runs_are_listed(pod):
    alice, the_pod = pod
    workflow = await alice.creates_a_workflow(in_pod=the_pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[{"id": "done", "type": "END"}], edges=[], in_pod=the_pod,
    )
    run = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

    runs = await alice.runs_of_workflow(workflow["name"], in_pod=the_pod)

    assert any(str(r["id"]) == str(run["id"]) for r in runs), runs


@scenario("A person watches a run as it happens")
@proves("PS-FLOW-020")
@covers("workflow.run.stream")
async def test_a_run_can_be_watched(pod):
    alice, the_pod = pod
    workflow = await alice.creates_a_workflow(in_pod=the_pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[{"id": "done", "type": "END"}], edges=[], in_pod=the_pod,
    )
    run = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

    status, content_type, _first = await alice.watches_run(run, in_pod=the_pod)

    assert status == 200, status
    assert "text/event-stream" in content_type, content_type


@scenario("Cancelling a run that has already finished is refused")
@proves("PS-FLOW-013")
@covers("workflow.run.cancel", "workflow.run.get")
async def test_cancelling_a_finished_run_is_refused(pod):
    alice, the_pod = pod
    workflow = await alice.creates_a_workflow(in_pod=the_pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[{"id": "done", "type": "END"}], edges=[], in_pod=the_pod,
    )
    run = await alice.runs_workflow(workflow["name"], in_pod=the_pod)
    assert run["status"] == "COMPLETED", run

    response = await alice.cancels_run(run, in_pod=the_pod)

    assert response.status_code >= 400, (
        f"a finished run must not be cancellable ({response.status_code})"
    )


@scenario("A person sees which runs are waiting on them")
@proves("PS-FLOW-012")
@covers("workflow.run.waiting_assigned_to_me")
async def test_waiting_runs_are_listed(pod):
    alice, the_pod = pod

    waiting = await alice.waits_assigned_to_me_in(the_pod)

    assert isinstance(waiting, list), waiting


@scenario("Answering a form on a run that is not waiting is refused")
@proves("PS-FLOW-012")
@covers("workflow.run.form.submit")
async def test_answering_a_run_that_is_not_waiting_is_refused(pod):
    alice, the_pod = pod
    workflow = await alice.creates_a_workflow(in_pod=the_pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[{"id": "done", "type": "END"}], edges=[], in_pod=the_pod,
    )
    run = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

    response = await alice.answers_form(
        run, node="done", inputs={"approved": True}, in_pod=the_pod
    )

    assert response.status_code >= 400, (
        f"a completed run is waiting on nobody ({response.status_code})"
    )


@scenario("A person sees the path a run actually took")
@proves("PS-FLOW-002")
@covers("workflow.run.visualize")
async def test_a_run_can_be_visualised(pod):
    alice, the_pod = pod
    workflow = await alice.creates_a_workflow(in_pod=the_pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[{"id": "done", "type": "END"}], edges=[], in_pod=the_pod,
    )
    run = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

    response = await alice.visualises_run(run, in_pod=the_pod)

    assert response.status_code == 200, response.text[:300]
