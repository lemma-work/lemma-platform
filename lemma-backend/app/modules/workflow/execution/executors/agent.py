"""Agent node executor: start the conversation and suspend on an AGENT wait."""

from app.modules.workflow.domain.errors import NodeExecutionError
from app.modules.workflow.domain.nodes import AgentNode
from app.modules.workflow.domain.wait import WaitRequest, WorkflowRunWaitType
from app.modules.workflow.execution.outcome import NodeOutcome, Suspend
from app.modules.workflow.execution.step_context import StepContext
from app.modules.workflow.execution.wait_ceiling import WAIT_TIMEOUT_SECONDS_KEY


class AgentExecutor:
    async def execute(self, node: AgentNode, step: StepContext) -> NodeOutcome:
        config = node.config
        input_data = step.context.resolve_inputs(config.input_mapping)
        # As a function step re-checks ``function.execute``: the run's person
        # must be able to run this agent themselves, now -- naming it in a
        # workflow they may start is not a grant to run it.
        if step.agent_access is None:
            raise RuntimeError("Agent steps need an access check to run")
        await step.agent_access.require_execute(
            agent_name=config.agent_name, pod_id=step.pod_id, user_id=step.user_id
        )

        conversation_id = await step.agent.run_agent(
            agent_name=config.agent_name,
            input_data=input_data,
            pod_id=step.pod_id,
            user_id=step.user_id,
            workflow_run_id=step.run_id,
            # Which workflow, not only which run: the title names the agent,
            # so a list of a pod's runs could only tell workflows apart by
            # parsing it.
            conversation_metadata={
                "workflow_id": str(step.flow_id),
                "workflow_name": step.flow_name,
                **_checkout(node, step),
            },
            instructions=config.instructions,
            model_name=config.model_name,
            generation=config.generation,
        )

        payload: dict[str, object] = {"agent_name": config.agent_name}
        if config.timeout_seconds is not None:
            payload[WAIT_TIMEOUT_SECONDS_KEY] = config.timeout_seconds
        return Suspend(
            wait=WaitRequest(
                wait_type=WorkflowRunWaitType.AGENT,
                external_ref=str(conversation_id),
                payload=payload,
            )
        )


def _checkout(node: AgentNode, step: StepContext) -> dict[str, object]:
    """The step's repository as the agent port takes one: ``repo`` in the
    conversation's metadata, the same key a webhook-started run carries.

    A malformed name is the run's error, said here, rather than a run that
    quietly starts in an empty folder: the agent module drops a ``repo`` it
    cannot parse, which suits a webhook's payload and not an author's step.
    This is the shape. The checkout itself is made in the sandbox before the
    agent's first command, after this step has started its run; one that did
    not land fails the step when the run settles, with git's reason.
    """
    config = node.config
    if config.repository is None:
        return {}
    bindings = {"repository": config.repository}
    if config.ref is not None:
        bindings["ref"] = config.ref
    values = step.context.resolve_inputs(bindings)
    owner, _, name = str(values.get("repository") or "").strip().partition("/")
    if not owner or not name or "/" in name:
        raise NodeExecutionError(
            node.id,
            f"repository resolved to {values.get('repository')!r}, expected 'owner/name'",
        )
    ref = values.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref.strip()):
        raise NodeExecutionError(
            node.id,
            f"ref resolved to {ref!r}, expected a branch, tag, sha or pull/<n>/head",
        )
    if isinstance(ref, str):
        ref = ref.strip()
    return {"repo": {"owner": owner, "repo": name, "ref": ref}}
