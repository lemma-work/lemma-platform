"""SQLAlchemy models for the unified agent module.

Split from a single module that had reached the 600-line ceiling. The seams are
the mapper's, not a filing preference: ``conversation`` holds the three classes
that name each other directly, and everything else depends on ``agent`` alone.

Import order below is load-bearing. ``conversation`` and ``feedback`` reference
``AgentModel`` as a class rather than by name, so ``agent`` has to be imported
first; re-exporting every submodule here is also what keeps ``migrations/env.py``
seeing the whole metadata from one import (see
``app/modules/test_support/test_migrations_metadata_contract.py``).
"""

from app.modules.agent.infrastructure.models.agent import AgentModel
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.models.feedback import (
    AgentFeedback,
    AgentFeedbackModel,
)
from app.modules.agent.infrastructure.models.pause import AgentPauseModel

__all__ = [
    "AgentFeedback",
    "AgentFeedbackModel",
    "AgentModel",
    "AgentPauseModel",
    "AgentRunModel",
    "ConversationModel",
    "MessageModel",
]
