"""Workflow module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.workflow.api.workflow_controller import router as workflow
    from app.modules.workflow.api.workflow_run_controller import router as workflow_run

    return [workflow, workflow_run]


def _register_work() -> None:
    # Resume, expire and start (FAST), decide and notify (JOB), and the
    # `FunctionRunFinished` subscription. Every process: the API advances runs
    # and so writes these, and emits the facts the subscription fans out.
    import app.modules.workflow.events.run_work  # noqa: F401
    import app.modules.workflow.events.step_jobs  # noqa: F401

    # An AGENT step resumes from the agent's `AgentRunFinished`, written in the
    # transaction that finished the run (API on a stop, worker otherwise).
    import app.modules.workflow.events.agent_resume_work  # noqa: F401

    # Every process: what answering a form on its line in a channel does
    # (``answers_for("wf")``) -- the API takes the answer.
    import app.modules.workflow.infrastructure.form_questions  # noqa: F401


def _register_periodic_work() -> None:
    import app.modules.workflow.events.periodic_work  # noqa: F401


def _resource_names():
    """How this module's resources are addressed by name in a grant.

    A thunk so the ORM import happens at assembly rather than whenever the
    module registry is imported. `app/core/authorization/resource_names.py`
    used to hold this table for every module at once.
    """
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.workflow.infrastructure.models import WorkflowModel

    return (
        (
            ResourceType.WORKFLOW,
            ResourceNameTable(
                WorkflowModel.id, WorkflowModel.pod_id, WorkflowModel.name
            ),
        ),
    )


def _live():
    from app.core.live.topics import LiveContribution
    from app.modules.workflow.services.org_waits import AssignedWaits

    return LiveContribution(org_states=(AssignedWaits(),))


module = LemmaModule(
    name="workflow",
    resource_names=_resource_names,
    routers=_routers,
    live=_live,
    register_work=_register_work,
    register_periodic_work=_register_periodic_work,
)
