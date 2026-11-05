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


class OrganizationDefaultConflictError(AgentModuleError):
    """Another default was chosen for the organization at the same moment.

    At most one profile carries the mark, and the database holds that; two
    owners choosing at once both pass every check before either commits, and
    the second write is refused here rather than leaving two defaults.
    """

    def __init__(
        self,
        message: str = (
            "The organization's default model was changed at the same time; "
            "reload and choose again"
        ),
    ):
        super().__init__(message, code="ORGANIZATION_DEFAULT_CONFLICT", status_code=409)


class RuntimeProfileValidationError(AgentModuleError):
    """A runtime profile create or edit the caller can correct.

    The message is always written for the person: a validator's text is
    passed through ``invalid_profile``, never a raw pydantic error, which
    quotes the input it refused. Callers that are not a request -- Agent Host
    admission, a run carrying selections across a harness republish -- catch
    this type by name.
    """

    def __init__(self, message: str, *, code: str = "RUNTIME_PROFILE_INVALID"):
        super().__init__(message, code=code, status_code=400)


class RuntimeProfileNotFoundError(AgentModuleError):
    """The profile is gone, or is not one this person can see."""

    def __init__(self, message: str = "Runtime profile not found"):
        super().__init__(message, code="RUNTIME_PROFILE_NOT_FOUND", status_code=404)


class RuntimeProfileNameTakenError(AgentModuleError):
    """Another profile in the same scope already has this name."""

    def __init__(self, name: str):
        super().__init__(
            f"Runtime profile named {name!r} already exists",
            code="RUNTIME_PROFILE_NAME_TAKEN",
            status_code=409,
        )


class RuntimeProfileConflictError(AgentModuleError):
    """Someone else changed this profile between this edit's read and its write.

    Writing anyway would put back every field the other edit changed -- a
    rotated key restored, an archived profile made active -- because an edit
    writes the whole row it read.
    """

    def __init__(
        self,
        message: str = (
            "These provider settings were changed by someone else while you "
            "were editing them; reload and try again"
        ),
    ):
        super().__init__(message, code="RUNTIME_PROFILE_CONFLICT", status_code=409)


class RuntimeProfileUnreadableError(AgentModuleError):
    """A stored profile whose configuration no longer validates.

    Raised instead of re-reading it as some other shape: that is how a harness
    profile used to lose its selections without anyone noticing.
    """

    def __init__(self) -> None:
        super().__init__(
            "This model's saved settings can't be read. Add it again in "
            "Settings → Models.",
            code="RUNTIME_PROFILE_UNREADABLE",
            status_code=409,
        )


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


class ApprovalNotOwnedError(AgentModuleError):
    """Only the person the agent works for may approve what it asked to do.

    An approved call runs with the conversation owner's authority, so a
    decision from anyone else -- a colleague in a shared channel typing
    "approve" -- would lend authority that is not theirs to lend.
    """

    def __init__(
        self,
        message: str = "Only the person this agent is working for can approve this",
    ):
        super().__init__(message, code="APPROVAL_NOT_OWNED", status_code=403)


class RuntimeUnavailableError(AgentModuleError):
    """The model, or the runtime profile, a teammate is set to use cannot run.

    One class, three codes, because the remedy is the same -- pick another
    model -- and the reader needs to know which part is wrong:

    * ``model_unavailable`` -- the profile no longer offers that model, or its
      provider just answered that it does not serve it;
    * ``runtime_profile_unavailable`` -- the profile is archived, gone, or
      someone else's personal one;
    * ``runtime_profile_not_shareable`` -- another member's own runtime (their
      Claude Code, their key), reached by someone who is not them. The owner
      may pin it to any teammate, a pod-visible one included; it runs for them.

    422 when a save names it, and the same error, not a substitute model, when
    a run reaches it.
    """

    def __init__(self, message: str, *, code: str = "model_unavailable"):
        super().__init__(message, code=code, status_code=422)


class HarnessNotFoundError(AgentModuleError):
    """No harness is registered for the requested kind.

    Internal misconfiguration (the kind comes from server-side registration), so
    this maps to 500. It is never returned directly from a controller today.
    """

    def __init__(self, message: str = "No harness registered"):
        super().__init__(message, code="HARNESS_NOT_FOUND", status_code=500)
