"""Agent adapters used by surface ingress and delivery."""

from app.modules.agent.api.dependencies import (
    AgentServiceDep,
    ConversationServiceDep,
    get_conversation_service,
)
from app.modules.agent.infrastructure.models import ConversationModel
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
    "ConversationModel",
    "ConversationService",
    "ConversationServiceDep",
    "get_conversation_service",
    "get_speech_provider",
    "is_datastore_path",
    "pod_services",
]
