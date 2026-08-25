"""Automating work → a run that stops to wait, and what happens when it resumes.

A workflow that pauses on a person is the ordinary case, not the exception:
approvals, reviews, and anything needing a number somebody has to look up. The
run has to survive the wait, resume where it stopped, and resume *once* — a
second submission of the same answer must not start the rest of the workflow a
second time.
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario
from harness.waiting import eventually

pytestmark = [
    journey("Automating work"),
    capability("Run a workflow"),
]


#: Two nodes is the smallest graph that can be *waiting* rather than merely
#: started: something to stop on, and something left to do afterwards.
def _asks_a_person(member_id: str) -> list[dict]:
    return [
        {
            "id": "ask",
            "type": "FORM",
            "config": {
                "input_schema": {
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                },
                # Without an assignee the wait belongs to nobody: it is not in
                # anyone's list and nobody is entitled to answer it.
                "assignee_pod_member_id": member_id,
            },
        },
        {"id": "done", "type": "END"},
    ]


THEN_FINISHES = [{"id": "ask_to_done", "source": "ask", "target": "done"}]


async def _a_waiting_run(alice, pod):
    membership = await alice.membership_of(alice, in_pod=pod)
    workflow = await alice.creates_a_workflow(in_pod=pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=_asks_a_person(membership["pod_member_id"]),
        edges=THEN_FINISHES,
        in_pod=pod,
    )
    run = await alice.runs_workflow(workflow["name"], in_pod=pod)
    waiting = await eventually(
        lambda: alice.api.get(f"/pods/{pod['id']}/workflow-runs/{run['id']}"),
        lambda state: str(state.get("status")).upper() == "WAITING",
        describe="the run to stop on the person it needs",
        timeout=60.0,
    )
    return workflow, waiting


@scenario("A run that stops on a person is held, and says who it is waiting for")
@proves("PS-FLOW-011", "PS-FLOW-012")
@covers("workflow.run.get", "workflow.run.waiting_assigned_to_me")
async def test_a_waiting_run_is_held(world):
    alice = await world.person("daniel")
    pod = await alice.works_in("operations")

    _workflow, run = await _a_waiting_run(alice, pod)

    mine = await alice.waits_assigned_to_me_in(pod)
    # Each entry pairs the wait with its run, so a person can see what is being
    # asked and which workflow is asking without a second call.
    assert any(
        str((item.get("run") or {}).get("id")) == str(run["id"]) for item in mine
    ), f"a run waiting on alice is not in the list of what waits on her: {mine}"
    [waiting] = [
        item for item in mine if str((item.get("run") or {}).get("id")) == str(run["id"])
    ]
    assert (waiting.get("wait") or {}).get("assigned_pod_member_id"), (
        f"the wait does not record who it is assigned to: {waiting}"
    )


@scenario("Answering resumes the run from where it stopped")
@proves("PS-FLOW-011")
@covers("workflow.run.form.submit", "workflow.run.get")
async def test_answering_resumes_the_run(world):
    alice = await world.person("daniel")
    pod = await alice.works_in("operations")
    _workflow, run = await _a_waiting_run(alice, pod)

    submitted = await alice.answers_form(
        run, node="ask", inputs={"approved": True}, in_pod=pod
    )
    assert submitted.status_code < 400, (
        f"the assigned person could not answer their own form: "
        f"{submitted.status_code} {submitted.text[:400]}"
    )

    finished = await eventually(
        lambda: alice.api.get(f"/pods/{pod['id']}/workflow-runs/{run['id']}"),
        lambda state: (
            str(state.get("status")).upper() in {"COMPLETED", "FAILED", "CANCELLED"}
        ),
        describe="the run to carry on once it was answered",
        timeout=60.0,
    )
    assert str(finished.get("status")).upper() == "COMPLETED", (
        f"the run resumed and then did not finish cleanly: {finished}"
    )


@scenario("The same answer submitted twice resumes the run once")
@proves("PS-FLOW-011")
@covers("workflow.run.form.submit", "workflow.run.get")
async def test_a_repeated_answer_resumes_once(world):
    alice = await world.person("daniel")
    pod = await alice.works_in("operations")
    _workflow, run = await _a_waiting_run(alice, pod)

    first = await alice.answers_form(
        run, node="ask", inputs={"approved": True}, in_pod=pod
    )
    assert first.status_code < 400, (
        f"the assigned person could not answer their own form: "
        f"{first.status_code} {first.text[:400]}"
    )
    again = await alice.answers_form(
        run, node="ask", inputs={"approved": True}, in_pod=pod
    )

    # The second submission must not be treated as a fresh completion. Whether
    # it is refused or absorbed is the product's choice; what it must not do is
    # run the rest of the workflow twice.
    finished = await eventually(
        lambda: alice.api.get(f"/pods/{pod['id']}/workflow-runs/{run['id']}"),
        lambda state: (
            str(state.get("status")).upper() in {"COMPLETED", "FAILED", "CANCELLED"}
        ),
        describe="the run to reach a terminal state",
        timeout=60.0,
    )
    assert str(finished.get("status")).upper() == "COMPLETED", (
        f"submitting the same answer twice broke the run: {finished} "
        f"(the second submission answered {again.status_code})"
    )

    steps = finished.get("step_history") or []
    ends = [step for step in steps if str(step.get("node_id")) == "done"]
    assert len(ends) <= 1, f"the workflow ran its remaining steps more than once: {ends}"


@scenario("A person stops a run that is still going")
@proves("PS-FLOW-013")
@covers("workflow.run.cancel", "workflow.run.get")
async def test_cancelling_a_live_run_stops_it(world):
    """The other half of PS-FLOW-013: stopping must work, not only be refused
    for runs that are already over. A run paused on a person is mid-flight —
    it has done work, it is holding a wait, and the person who started it is
    entitled to call the whole thing off rather than answer it."""
    alice = await world.person("daniel")
    pod = await alice.works_in("operations")

    _workflow, run = await _a_waiting_run(alice, pod)

    cancelled = await alice.cancels_run(run, in_pod=pod)
    assert cancelled.status_code < 400, (
        f"stopping a live run answered {cancelled.status_code}: {cancelled.text[:300]}"
    )

    stopped = await eventually(
        lambda: alice.api.get(f"/pods/{pod['id']}/workflow-runs/{run['id']}"),
        lambda state: str(state.get("status")).upper() == "CANCELLED",
        describe="the run to report itself stopped",
        timeout=60.0,
    )
    assert str(stopped.get("status")).upper() == "CANCELLED", stopped


@scenario("Somebody the run did not ask cannot answer for them")
@proves("PS-FLOW-012")
@covers("workflow.run.form.submit", "workflow.run.get")
async def test_a_person_who_was_not_asked_cannot_answer(world):
    """A wait names who it is for, and that name has to mean something.

    The run stops precisely because a particular person's decision is needed —
    an approval, a number, a choice between branches. If anyone with pod access
    can submit it, the assignment is decoration and the audit trail records a
    decision the named person never made.

    Distinct from answering a run that is not waiting: here the run *is*
    waiting, the form *is* valid, and the only thing wrong is who is filling
    it in.
    """
    alice = await world.person("daniel")
    pod = await alice.works_in("operations")
    _workflow, run = await _a_waiting_run(alice, pod)

    somebody_else = await world.person("sofia")
    submitted = await somebody_else.answers_form(
        run, node="ask", inputs={"approved": True}, in_pod=pod
    )

    assert submitted.status_code >= 400, (
        f"a colleague the run never asked answered for the assigned person "
        f"({submitted.status_code})"
    )
    state = await alice.api.get(f"/pods/{pod['id']}/workflow-runs/{run['id']}")
    assert str(state.get("status")).upper() not in {"COMPLETED", "FAILED"}, (
        f"the refusal did not stop the run being resumed anyway: {state}"
    )
