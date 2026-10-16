"""Workflow module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.workflow.api.workflow_controller import router as workflow
    from app.modules.workflow.api.workflow_run_controller import router as workflow_run

    return [workflow, workflow_run]


def _event_routers():
    # handlers.py also defines the module's remaining streaq tasks and crons,
    # which register on import.
    from app.modules.workflow.events.handlers import router

    return [router]


def _register_work() -> None:
    # Resume, expire and start (FAST), decide and notify (JOB), and the
    # `FunctionRunFinished` subscription. Every process: the API advances runs
    # and so writes these, and emits the facts the subscription fans out.
    import app.modules.workflow.events.run_work  # noqa: F401
    import app.modules.workflow.events.step_jobs  # noqa: F401


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


module = LemmaModule(
    name="workflow",
    resource_names=_resource_names,
    routers=_routers,
    event_routers=_event_routers,
    register_work=_register_work,
    stream_groups=(
        ("agent_events", "workflow-agent-events"),
        ("schedule_events", "workflow-schedule-events"),
    ),
)
