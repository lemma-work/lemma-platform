"""How a pod's agents are offered to an MCP host: which ones, to whom, as what.

The presenting half of ``pod_mcp_agents``, which runs the asks: the two tools'
names, schemas and annotations, the agents a person may ask, and which one a
call names. See ``docs/architecture/mcp-plugin.md`` §e.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from mcp.types import Tool, ToolAnnotations
from pydantic import BaseModel, Field

from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.mcp_access.contracts import McpPrincipal, Scope

ASK_AGENT = "lemma_pod_ask_agent"
AGENT_ANSWER = "lemma_pod_agent_answer"
AGENT_TOOLS = frozenset({ASK_AGENT, AGENT_ANSWER})

#: The same ceiling as apps: past it the description is a directory, not a
#: choice, and the rest are one "Open in Lemma" away.
MAX_AGENTS = 20
#: How long a call holds the host's tool call open for a quick answer. Below
#: what hosts allow a tool call, so a slow run comes back as WORKING rather
#: than as the host giving up with nothing to show.
DEFAULT_WAIT_SECONDS = 25.0
MAX_WAIT_SECONDS = 50.0
MAX_REQUEST_CHARACTERS = 8000
#: An agent's description, as far as the tool's description carries it.
_DESCRIPTION_CHARACTERS = 200

_ASK_ANNOTATIONS = ToolAnnotations(
    title="Ask one of the pod's agents",
    read_only_hint=False,
    # The agent can write, run workflows and reach connected accounts, all as
    # the person: every one of the cautious answers.
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)
_ANSWER_ANNOTATIONS = ToolAnnotations(
    title="Check on an agent's answer",
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


class AgentAskRefused(Exception):
    """An ask or a check that cannot be made, with the reason the model reads."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class AskableAgent:
    id: UUID
    name: str
    description: str | None
    is_assistant: bool

    @property
    def conversation_agent_name(self) -> str | None:
        """How a conversation names it: by nothing, for the pod's own assistant,
        as every chat opened with it in Lemma does."""
        return None if self.is_assistant else self.name


class AskAgentArguments(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_REQUEST_CHARACTERS)
    agent: str | None = None
    conversation_id: UUID | None = None
    wait_seconds: float = Field(
        default=DEFAULT_WAIT_SECONDS, ge=0.0, le=MAX_WAIT_SECONDS
    )


class AgentAnswerArguments(BaseModel):
    conversation_id: UUID
    wait_seconds: float = Field(
        default=DEFAULT_WAIT_SECONDS, ge=0.0, le=MAX_WAIT_SECONDS
    )


def offers_agents(principal: McpPrincipal | None) -> bool:
    """Asking an agent is for an outside client's connection that may change
    things: the agent acts as the person, and is not narrowed by the
    connection's scopes."""
    return principal is not None and Scope.WRITE in principal.scopes


def is_agent_tool(name: str) -> bool:
    return name in AGENT_TOOLS


def askable_agents(agents: Sequence[Agent]) -> list[AskableAgent]:
    """The agents the person may run, the pod's assistant first, then by name."""
    runnable = [
        AskableAgent(
            id=agent.id,
            name=agent.name,
            description=agent.description,
            is_assistant=agent.kind is AgentKind.POD_DEFAULT,
        )
        for agent in agents
        if Permissions.AGENT_EXECUTE in agent.allowed_actions
    ]
    runnable.sort(key=lambda agent: (not agent.is_assistant, agent.name.casefold()))
    return runnable[:MAX_AGENTS]


def resolve_agent(agents: Sequence[AskableAgent], said: str | None) -> AskableAgent:
    """The agent a call named, or the pod's assistant when it named none."""
    if not agents:
        raise AgentAskRefused("There is no agent in this pod you may ask.")
    if said is None or not said.strip():
        assistant = next((agent for agent in agents if agent.is_assistant), None)
        if assistant is None:
            raise AgentAskRefused(
                f"Name the agent to ask: {', '.join(a.name for a in agents)}."
            )
        return assistant
    said = said.strip()
    match = next((agent for agent in agents if agent.name == said), None) or next(
        (agent for agent in agents if agent.name.casefold() == said.casefold()),
        None,
    )
    if match is None:
        raise AgentAskRefused(
            f"No agent you may ask is called {said!r}. You may ask: "
            f"{', '.join(agent.name for agent in agents)}."
        )
    return match


def agent_tools(agents: Sequence[AskableAgent]) -> list[Tool]:
    if not agents:
        return []
    listing = "\n".join(_listed(agent) for agent in agents)
    return [
        Tool(
            name=ASK_AGENT,
            title=_ASK_ANNOTATIONS.title,
            description=(
                "Hand a request to one of this pod's agents and get its answer. "
                "Each works in its own conversation in Lemma, as the person, with "
                "its own tools -- functions, workflows, connected accounts -- that "
                "the other tools here do not reach. Its usage counts as theirs, "
                "as a chat they start in Lemma does.\n\n"
                f"The agents:\n{listing}\n\n"
                "A quick answer comes back in this call. If the status is WORKING, "
                f"call {AGENT_ANSWER} with the conversation_id to wait for it. To "
                "follow up, or to answer a question it asked, call this again "
                "with the same conversation_id."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_REQUEST_CHARACTERS,
                        "description": (
                            "Everything the agent needs, in one self-contained "
                            "message. It sees nothing of this conversation: name "
                            "the record, the dates, the customer, and say what "
                            "answer you want back."
                        ),
                    },
                    "agent": {
                        "type": "string",
                        "enum": [agent.name for agent in agents],
                        "description": (
                            "Which agent to ask. Leave it out to ask the pod's "
                            "own assistant. Ignored when continuing a conversation."
                        ),
                    },
                    "conversation_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": (
                            "Continue this conversation, from an earlier call, "
                            "instead of starting a new one."
                        ),
                    },
                    "wait_seconds": _wait_schema(),
                },
                "required": ["message"],
            },
            annotations=_ASK_ANNOTATIONS,
        ),
        Tool(
            name=AGENT_ANSWER,
            title=_ANSWER_ANNOTATIONS.title,
            description=(
                f"Wait for, or read, the answer to a request {ASK_AGENT} handed to "
                "one of the pod's agents."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": f"The conversation_id {ASK_AGENT} returned.",
                    },
                    "wait_seconds": _wait_schema(),
                },
                "required": ["conversation_id"],
            },
            annotations=_ANSWER_ANNOTATIONS,
        ),
    ]


def _listed(agent: AskableAgent) -> str:
    about = (agent.description or "").strip().replace("\n", " ")
    if len(about) > _DESCRIPTION_CHARACTERS:
        about = about[: _DESCRIPTION_CHARACTERS - 1].rstrip() + "…"
    if agent.is_assistant:
        about = f"The pod's own assistant. {about}".strip()
    return f"- {agent.name}: {about}" if about else f"- {agent.name}"


def _wait_schema() -> JsonObject:
    return {
        "type": "number",
        "minimum": 0,
        "maximum": MAX_WAIT_SECONDS,
        "default": DEFAULT_WAIT_SECONDS,
        "description": "How long to wait here for the answer before returning.",
    }
