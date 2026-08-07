"""Agent surface domain/application errors."""

from uuid import UUID

from app.core.domain.errors import DomainError


class AgentSurfaceError(DomainError):
    def __init__(
        self,
        message: str,
        code: str = "AGENT_SURFACE_ERROR",
        status_code: int = 400,
    ):
        super().__init__(message=message, code=code, status_code=status_code)


class AgentSurfaceValidationError(AgentSurfaceError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="AGENT_SURFACE_VALIDATION_ERROR",
            status_code=422,
        )


class AgentSurfaceCredentialConflictError(AgentSurfaceError):
    """The credential this surface wants is already spoken for in the org.

    Carries *which* surface holds it in ``details`` so the setup UI can name the
    pod and link to it, rather than showing a dead-end message. The claim is also
    published up front by the available-surfaces catalog (``system_claim``); this
    is the race/stale-cache backstop on the write path.
    """

    def __init__(
        self,
        message: str,
        *,
        pod_id: UUID,
        surface_name: str,
        kind: str,
    ):
        super().__init__(
            message=message,
            code="AGENT_SURFACE_CREDENTIAL_CONFLICT",
            status_code=409,
        )
        self.details = {
            # "SYSTEM" (the shared Lemma bot/number) or "ACCOUNT" (a connected
            # account already bound elsewhere) — they read differently in the UI.
            "kind": kind,
            "conflicting_surface": {
                "pod_id": str(pod_id),
                "name": surface_name,
            },
        }


class AgentSurfaceNotFoundError(AgentSurfaceError):
    def __init__(self, surface_id: str):
        super().__init__(
            message=f"Agent surface '{surface_id}' not found",
            code="AGENT_SURFACE_NOT_FOUND",
            status_code=404,
        )


class AgentSurfaceAlreadyExistsError(AgentSurfaceError):
    """A surface with the same stable name already exists in this pod."""

    def __init__(self, name: str):
        super().__init__(
            message=f"Surface '{name}' already exists in this pod",
            code="AGENT_SURFACE_ALREADY_EXISTS",
            status_code=409,
        )


class AgentSurfacePlatformError(AgentSurfaceError):
    def __init__(self, platform: str, message: str):
        super().__init__(
            message=f"Surface platform '{platform}' error: {message}",
            code="AGENT_SURFACE_PLATFORM_ERROR",
            status_code=400,
        )


class AgentSurfaceRoutingError(AgentSurfaceError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="AGENT_SURFACE_ROUTING_ERROR",
            status_code=400,
        )


class AgentSurfaceCredentialError(AgentSurfaceError):
    def __init__(self, platform: str, message: str):
        super().__init__(
            message=f"Surface credentials for '{platform}' error: {message}",
            code="AGENT_SURFACE_CREDENTIAL_ERROR",
            status_code=400,
        )


class NotificationNotFoundError(AgentSurfaceError):
    def __init__(self, notification_id: str):
        super().__init__(
            message=f"Notification '{notification_id}' not found",
            code="NOTIFICATION_NOT_FOUND",
            status_code=404,
        )


class NotificationTransitionError(AgentSurfaceError):
    """An illegal move on a notification's lifecycle.

    A notification owns the ask from the moment it is created until it resolves,
    so *it* decides which transitions are legal — not the controller, not the
    tool, not the surface adapter. Every one of them ends up here, and every one
    of them is a 409: the request was well-formed, the row is simply not in a
    state where it can be honoured.

    ``details`` carries the current status so a caller (or a UI that raced
    another tab) can re-render without a second round trip.
    """

    def __init__(self, message: str, *, notification_id: UUID, status: str):
        super().__init__(
            message=message,
            code="NOTIFICATION_INVALID_TRANSITION",
            status_code=409,
        )
        self.details = {
            "notification_id": str(notification_id),
            "status": status,
        }


class TelegramManagerNotConfiguredError(AgentSurfaceError):
    def __init__(self):
        super().__init__(
            message="Telegram managed-bot provisioning is not configured",
            code="TELEGRAM_MANAGER_NOT_CONFIGURED",
            status_code=503,
        )


class TelegramManagedBotSetupNotFoundError(AgentSurfaceError):
    def __init__(self, setup_id: str):
        super().__init__(
            message=f"Telegram managed-bot setup '{setup_id}' not found",
            code="TELEGRAM_MANAGED_BOT_SETUP_NOT_FOUND",
            status_code=404,
        )


class TelegramManagedBotSetupAlreadyInProgressError(AgentSurfaceError):
    def __init__(self, surface_name: str):
        super().__init__(
            message=(
                f"Telegram managed-bot setup for surface '{surface_name}' "
                "is already in progress"
            ),
            code="TELEGRAM_MANAGED_BOT_SETUP_ALREADY_IN_PROGRESS",
            status_code=409,
        )
