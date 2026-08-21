"""Persistence for the agent module.

One file per aggregate. They were one 1,117-line module, which put runtime
profiles, agents and conversations — three things with no relationship to each
other — behind a single import.

The package keeps that import working: every caller says
`from app.modules.agent.infrastructure.repositories import X`, and none of the
37 of them had to move.
"""

from app.modules.agent.infrastructure.repositories.agent_repository import (
    AgentRepository,
)
from app.modules.agent.infrastructure.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.agent.infrastructure.repositories.runtime_profile_repository import (
    AgentRuntimeProfileRepository,
)

__all__ = [
    "AgentRepository",
    "AgentRuntimeProfileRepository",
    "ConversationRepository",
]
