"""Agent adapters `agent_surfaces` still reaches for, and why each is left.

Two of what was here have gone to the module that owns them:
`ConversationModel` -- which `agent_surfaces` was loading in order to *write* a
column on it -- is now `merge_conversation_metadata` in
`agent/contracts/conversations.py`, and the schedule shim beside this file is
gone entirely because both things it published were dead.

What is left needs a home in `agent`, and each needs a different one:

- `ConversationService` is the hard case. Twenty call sites in `agent_surfaces`
  reach through it into `conversation_repository`, `agent_repository`,
  `authorization_service`, `usage_service`, `fallback_model_name` and `uow` --
  `services/interaction_helpers.py` builds a second service out of six of them.
  Publishing operations means naming every one of those, which is its own
  change and not a side effect of this one.
- `AgentServiceDep` is two lookups, both used with no authorization arguments
  at all: "the id of the agent with this name" and "the name of the agent with
  this id". They belong beside `list_agent_summaries_by_pod` in
  `agent/contracts/pod_summaries.py`, or in an `agent/contracts/agents.py`.
- `is_datastore_path` / `pod_services` reach `agent`'s tool-context plumbing to
  read a pod file for an outbound email attachment. Datastore already publishes
  the reads (`datastore/contracts/surfaces.py`); what is missing is the
  authorization context, which today only a tool's `deps` can produce.
- `get_speech_provider` is one factory, and wants an `agent/contracts/speech.py`.
"""

from app.modules.agent.api.dependencies import (
    AgentServiceDep,
    ConversationServiceDep,
    get_conversation_service,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.tools.file_access import is_datastore_path
from app.modules.agent.tools.pod.pod_data_access import pod_services


def get_speech_provider():
    from app.modules.agent.tools.speech.provider import (
        get_speech_provider as resolve_provider,
    )

    return resolve_provider()


__all__ = [
    "AgentServiceDep",
    "ConversationService",
    "ConversationServiceDep",
    "get_conversation_service",
    "get_speech_provider",
    "is_datastore_path",
    "pod_services",
]
