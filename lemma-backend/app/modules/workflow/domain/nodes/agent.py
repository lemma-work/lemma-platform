"""Agent node configuration."""

from typing import Dict, Literal

from pydantic import BaseModel, Field, model_validator

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
    repository: InputBinding | None = Field(
        default=None,
        description=(
            "The GitHub repository the agent works in, as `owner/name`. The "
            "run starts in a worktree of it, cloned through the pod's `github` "
            "connector as the run's person, as a webhook-started run does. "
            "Usually an expression, e.g. `start.payload.repository.full_name`."
        ),
    )
    ref: InputBinding | None = Field(
        default=None,
        description=(
            "Where that worktree starts: a branch, a tag, a commit sha, or a "
            "pull request's head as `pull/<n>/head`. Unset is the repository's "
            "default branch. Needs `repository`."
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

    @model_validator(mode="after")
    def _ref_needs_a_repository(self) -> "AgentNodeConfig":
        if self.ref is not None and self.repository is None:
            raise ValueError(
                "`ref` says where to check a repository out; set `repository` too"
            )
        return self


class AgentNode(BaseNode):
    """Agent node. The run waits on the agent conversation to complete."""

    type: Literal[NodeType.AGENT] = NodeType.AGENT
    config: AgentNodeConfig
