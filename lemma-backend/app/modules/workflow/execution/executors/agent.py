"""Agent node executor: start the conversation and suspend on an AGENT wait."""

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
