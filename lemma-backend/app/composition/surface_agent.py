"""The one agent service `agent_surfaces` still reaches for.

Everything else that was here has gone to the module that owns it:
`AgentServiceDep` is `agent/contracts/agents.py` -- two lookups, one of which
was reaching a repository off the service to read a name -- `get_speech_provider`
is `agent/contracts/speech.py`, and the pod-file pair behind an email
attachment is `agent/contracts/pod_files.py`, which publishes the run's
authorization rather than datastore's services.

`ConversationService` is the hard case and the reason this file is still here.
Twenty call sites in `agent_surfaces` reach through it into
`conversation_repository`, `agent_repository`, `authorization_service`,
`usage_service`, `fallback_model_name` and `uow` -- `services/interaction_helpers.py`
builds a second service out of six of them. Publishing operations means naming
every one of those, which is its own change and not a side effect of this one.
"""

from app.modules.agent.api.dependencies import (
    ConversationServiceDep,
    get_conversation_service,
)
from app.modules.agent.services.conversation_service import ConversationService

__all__ = [
    "ConversationService",
    "ConversationServiceDep",
    "get_conversation_service",
]
