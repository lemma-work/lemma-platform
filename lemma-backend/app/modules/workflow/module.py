"""Workflow module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.workflow.api.workflow_controller import router as workflow
    from app.modules.workflow.api.workflow_run_controller import router as workflow_run

    return [workflow, workflow_run]


def _event_routers():
    # handlers.py also defines 4 streaq tasks/crons that register on import.
    from app.modules.workflow.events.handlers import router

    return [router]


module = LemmaModule(
    name="workflow",
    routers=_routers,
    event_routers=_event_routers,
    stream_groups=(
        ("function_run_events", "workflow-function-events"),
        ("agent_events", "workflow-agent-events"),
        ("schedule_events", "workflow-schedule-events"),
    ),
)
