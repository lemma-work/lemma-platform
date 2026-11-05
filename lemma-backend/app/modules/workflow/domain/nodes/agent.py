"""Agent node configuration."""

from typing import Dict, Literal

from pydantic import BaseModel, Field

from app.core.domain.generation import GenerationSettings
from app.modules.workflow.domain.nodes.base import BaseNode, NodeType
from app.modules.workflow.domain.nodes.bindings import InputBinding

#: The longest an author may give one agent step. A step longer than a week is
#: a schedule, not a step.
MAX_AGENT_STEP_TIMEOUT_SECONDS = 7 * 24 * 3600


class AgentNodeConfig(BaseModel):
    """Configuration for Agent node."""

    agent_name: str = Field(
        ...,
        min_length=1,
        description="Agent resource name to execute.",
        examples=["expense-parser"],
    )
    input_mapping: Dict[str, InputBinding] = Field(
        default_factory=dict,
        description=(
            "Explicit mapping from agent input key to either an expression or "
            "a literal JSON value. Strings are never auto-interpreted."
        ),
        json_schema_extra={
            "example": {
                "issue_key": {"type": "expression", "value": "start.payload.issue.key"},
                "channel": {"type": "literal", "value": "finance"},
            }
        },
    )
    instructions: str | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
        description=(
            "What this step is for. Added to the agent's own instructions for "
            "this run only; the agent keeps its identity and gains a task."
        ),
    )
    model_name: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Another model from the agent's runtime profile, for this step "
            "only. Unset runs on the agent's model."
        ),
    )
    generation: GenerationSettings | None = Field(
        default=None,
        description=(
            "How the model generates for this step: reasoning effort, output "
            "cap, temperature. Each field set here replaces the agent's; each "
            "left unset keeps it."
        ),
    )
    timeout_seconds: int | None = Field(
        default=None,
        # The expiry sweep looks only at waits older than ten minutes, so a
        # shorter timeout would be a promise it cannot keep.
        ge=600,
        le=MAX_AGENT_STEP_TIMEOUT_SECONDS,
        description=(
            "How long the step may run before the workflow stops it, in place "
            "of the deployment's ceiling. Checked by a periodic sweep, so the "
            "step ends within minutes of it, not to the second. A step waiting "
            "on a person gets the usual multiple of it."
        ),
    )


class AgentNode(BaseNode):
    """Agent node. The run waits on the agent conversation to complete."""

    type: Literal[NodeType.AGENT] = NodeType.AGENT
    config: AgentNodeConfig
