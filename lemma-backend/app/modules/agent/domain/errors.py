"""Agent domain/service errors.

These subclass ``DomainError`` so the global exception handler translates them to
the right HTTP status automatically — controllers do not need to catch them and
re-raise as ``HTTPException``. They are also raised in non-HTTP contexts (agent
runs in background workers, the workflow agent adapter); that is harmless since
``DomainError`` is still a plain ``Exception`` and only the HTTP boundary reads
``status_code``/``code``.
"""

from app.core.domain.errors import DomainError


class AgentModuleError(DomainError):
    """Base error for the unified agent module."""

    def __init__(
        self,
        message: str = "Agent error",
        *,
        code: str = "AGENT_ERROR",
        status_code: int = 400,
    ):
        super().__init__(message, code=code, status_code=status_code)


class AgentNotFoundError(AgentModuleError):
    """Requested agent was not found."""

    def __init__(self, message: str = "Agent not found"):
        super().__init__(message, code="AGENT_NOT_FOUND", status_code=404)


class AgentAlreadyExistsError(AgentModuleError):
    """A pod agent with the same stable name already exists."""

    def __init__(self, message: str = "Agent already exists"):
        super().__init__(message, code="AGENT_ALREADY_EXISTS", status_code=409)


class AgentValidationError(AgentModuleError):
    """Agent payload failed module-level validation."""

    def __init__(self, message: str = "Invalid agent"):
        super().__init__(message, code="AGENT_VALIDATION_ERROR", status_code=400)


class ConversationNotFoundError(AgentModuleError):
    """Requested conversation was not found or not visible."""

    def __init__(self, message: str = "Conversation not found"):
        super().__init__(message, code="CONVERSATION_NOT_FOUND", status_code=404)


class ConversationValidationError(AgentModuleError):
    """Conversation payload failed module-level validation."""

    def __init__(self, message: str = "Invalid conversation"):
        super().__init__(message, code="CONVERSATION_VALIDATION_ERROR", status_code=400)


class ConversationStateError(AgentModuleError):
    """Conversation state does not allow the requested operation."""

    def __init__(self, message: str = "Conversation is in an invalid state"):
        super().__init__(message, code="CONVERSATION_STATE_ERROR", status_code=409)


class UnknownApprovalError(AgentModuleError):
    """No pending approval and no recorded decision for this approval id.

    The approval genuinely does not exist (or its call was never persisted), so
    it can neither be resolved nor reconciled. Distinct from a resolvable
    already-recorded decision, which self-heals instead of erroring.
    """

    def __init__(self, message: str = "Unknown or expired approval"):
        super().__init__(message, code="UNKNOWN_APPROVAL", status_code=404)


class HarnessNotFoundError(AgentModuleError):
    """No harness is registered for the requested kind.

    Internal misconfiguration (the kind comes from server-side registration), so
    this maps to 500. It is never returned directly from a controller today.
    """

    def __init__(self, message: str = "No harness registered"):
        super().__init__(message, code="HARNESS_NOT_FOUND", status_code=500)
