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


class AgentSurfaceRuntimeUnsupportedError(AgentSurfaceValidationError):
    """This runtime has no way for the platform to receive.

    Its own code, and that is the entire point. The provisioning log deliberately
    records ``failure_code`` rather than the exception text -- the pipeline
    strips any ``error`` field, because exception messages carry keys and
    personal data -- on the understanding that the code names the branch that
    refused. ``AGENT_SURFACE_VALIDATION_ERROR`` did not: a dozen branches raise
    it, so a pod silently getting no mailbox looked identical to a bad name or a
    mode mismatch, and telling them apart took a database and a code read.

    Still a validation error, so every existing handler and status code is
    unchanged.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "AGENT_SURFACE_RUNTIME_UNSUPPORTED"


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
            # "SYSTEM" (the shared Lemma bot/number), "ACCOUNT" (a connected
            # account already bound elsewhere), or "IDENTITY" (the bot itself is
            # already somebody's) — they read differently in the UI, and only
            # the last is answered by adding a second bot rather than by
            # releasing something.
            "kind": kind,
            "conflicting_surface": {
                "pod_id": str(pod_id),
                "name": surface_name,
            },
        }


class AgentSurfaceNumberPoolExhaustedError(AgentSurfaceError):
    """Every WhatsApp number the deployment owns is already held.

    **503 and not 409**, which is the whole point of giving it its own class.
    A conflict says "somebody else has the thing you asked for, take it up with
    them" -- that is `AgentSurfaceCredentialConflictError`, and it names the pod
    holding it so the UI can link there. This organisation conflicts with
    nobody: it asked for *a* number and the deployment has none left to give.
    There is no other party and nothing the caller can do differently, so the
    honest answer is that the service cannot serve the request right now and
    somebody has to buy more numbers.

    Exhaustion is a steady state rather than a failure. A pool is bought a
    number at a time and can sit fully allocated for weeks, reached entirely
    through ordinary success -- so it is not logged as degraded where it is
    detected. Only the caller knows whether it is failing a person's request,
    which is this, or quietly falling back to the shared line, which is not.
    """

    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="AGENT_SURFACE_NUMBER_POOL_EXHAUSTED",
            status_code=503,
        )


class AgentSurfaceNotFoundError(AgentSurfaceError):
    def __init__(self, surface_id: str):
        super().__init__(
            message=f"Agent surface '{surface_id}' not found",
            code="AGENT_SURFACE_NOT_FOUND",
            status_code=404,
        )


class AgentSurfacePodNotFoundError(AgentSurfaceError):
    """The pod a surface request names has no row -- deleted mid-request, or
    never there. A 404 like any other missing resource, not a server error."""

    def __init__(self, pod_id: str):
        super().__init__(
            message=f"Pod '{pod_id}' not found",
            code="AGENT_SURFACE_POD_NOT_FOUND",
            status_code=404,
        )


class AgentSurfaceAlreadyExistsError(AgentSurfaceError):
    """A surface with the same stable name already exists in this pod."""

    def __init__(self, name: str):
        super().__init__(
            message=(
                f"Surface '{name}' already exists in this pod. Update it by that "
                f"name (PATCH /surfaces/{name}) rather than creating it again."
            ),
            code="AGENT_SURFACE_ALREADY_EXISTS",
            status_code=409,
        )
        self.details = {"existing_surface": {"name": name}}


class AgentSurfaceAgentPlatformConflictError(AgentSurfaceError):
    """This agent already reaches this platform somewhere else.

    The database says the same thing through ``uq_agent_surface_agent_type``.
    Without this the constraint was the only thing saying it, and an
    IntegrityError arrives after the transaction is already unusable -- so a
    person creating a second Slack surface for one agent got a 500 instead of
    being told what the rule is.
    """

    def __init__(self, *, platform: str, pod_id: UUID, surface_name: str):
        # Resend gets its own sentence. "Pick another agent" is advice for
        # somebody choosing where to install a Slack app; for a mailbox it is
        # close to nonsense, because the agent did not choose to have one -- it
        # was given one when it was created, under a name derived from its own.
        # The commonest way to meet this error is a hand-written pod bundle
        # naming an agent's mailbox something else, and the thing that person
        # needs to know is the name to use. See `DEV-SURF-003`.
        advice = (
            f"Every agent is given a mailbox when it is created, named "
            f"'{surface_name}'. Change that surface rather than creating another "
            f"(PATCH /surfaces/{surface_name}; in a pod bundle, name the surface "
            f"'{surface_name}'), or connect email without a name to be handed the "
            "address it already has."
            if platform.upper() == "RESEND"
            else (
                "An agent reaches a platform in one place: one Slack app, one "
                "WhatsApp number, one Telegram bot. Pick another agent, or "
                "change the surface it already has."
            )
        )
        super().__init__(
            message=(
                f"This agent already has a {platform.title()} surface "
                f"('{surface_name}'). {advice}"
            ),
            code="AGENT_SURFACE_AGENT_PLATFORM_CONFLICT",
            status_code=409,
        )
        self.details = {
            "conflicting_surface": {
                "pod_id": str(pod_id),
                "name": surface_name,
            },
        }


class AgentSurfacePlatformError(AgentSurfaceError):
    def __init__(self, platform: str, message: str):
        super().__init__(
            message=f"Surface platform '{platform}' error: {message}",
            code="AGENT_SURFACE_PLATFORM_ERROR",
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


class OutsideAnswerNeedsApproval(AgentSurfaceError):
    """An answer to someone outside the pod, not yet confirmed by its recipient.

    Its words go back to a stranger, and an agent drafting them acts with all
    of the member's access. So nothing is recorded until the member approves
    the exact text (``request_approval``) or types it themselves.
    """

    def __init__(self, *, notification_id: UUID):
        super().__init__(
            message=(
                "This question came from someone outside the pod. Its answer is "
                "passed back only once the person it was sent to approves the "
                "exact words."
            ),
            code="OUTSIDE_ANSWER_NEEDS_APPROVAL",
            status_code=403,
        )
        self.details = {"notification_id": str(notification_id)}


class SlackAppCreationForbiddenError(AgentSurfaceError):
    """Only someone who may add the org's connector installs may make Slack apps."""

    def __init__(self) -> None:
        super().__init__(
            message=(
                "Only an organization owner or editor can let Lemma create "
                "Slack apps, or create one."
            ),
            code="SLACK_APP_CREATION_FORBIDDEN",
            status_code=403,
        )


class SlackAppCreationNotSetUpError(AgentSurfaceError):
    """No configuration token is saved for that Slack workspace."""

    def __init__(self, team_id: str) -> None:
        super().__init__(
            message=(
                "Lemma cannot create Slack apps in that workspace yet. Save a "
                "configuration token for it first."
            ),
            code="SLACK_APP_CREATION_NOT_SET_UP",
            status_code=409,
        )
        self.details = {"team_id": team_id}


class SlackConfigurationTokenRefusedError(AgentSurfaceError):
    """Slack would not rotate the refresh token a person just pasted."""

    def __init__(self, slack_error: str) -> None:
        super().__init__(
            message=(
                f"Slack did not accept that refresh token ({slack_error}). "
                "Generate a new configuration token on api.slack.com/apps and "
                "paste its refresh token."
            ),
            code="SLACK_CONFIGURATION_TOKEN_REFUSED",
            status_code=422,
        )
        self.details = {"slack_error": slack_error}


class SlackAppCreationTokenExpiredError(AgentSurfaceError):
    """The saved configuration token can no longer be used: Slack refused its
    refresh token, or Lemma can no longer decrypt it. Either way, only a new
    paste brings it back."""

    def __init__(self, team_id: str, *, unreadable: bool = False) -> None:
        cause = (
            "Lemma can no longer read the configuration token saved"
            if unreadable
            else "Slack no longer accepts the configuration token saved"
        )
        super().__init__(
            message=(
                f"{cause} for that workspace. Someone who may manage the "
                "organization's connectors needs to generate a new one on "
                "api.slack.com/apps and paste its refresh token."
            ),
            code="SLACK_APP_CREATION_TOKEN_EXPIRED",
            status_code=409,
        )
        self.details = {"team_id": team_id}


class SlackAppCreationBusyError(AgentSurfaceError):
    """Another request holds what this one needs; retrying shortly succeeds."""

    def __init__(self, team_id: str, *, creating: bool = False) -> None:
        super().__init__(
            message=(
                "That app is already being created. Try again in a moment."
                if creating
                else "Lemma is refreshing its Slack token for that workspace. "
                "Try again in a moment."
            ),
            code="SLACK_APP_CREATION_BUSY",
            status_code=409,
        )
        self.details = {"team_id": team_id, "retryable": True}


class SlackRateLimitedError(AgentSurfaceError):
    """Slack asked Lemma to slow down; the request may be retried after a wait."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            message=(
                "Slack is limiting how fast apps can be made in that workspace. "
                f"Try again in {retry_after_seconds} seconds."
            ),
            code="SLACK_RATE_LIMITED",
            status_code=429,
        )
        self.details = {"retry_after_seconds": retry_after_seconds}


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


class SurfaceWebhookAuthenticationError(DomainError):
    """Webhook signature / authenticity check failed.

    A ``DomainError`` so the global handler translates it automatically (no
    manual catch-and-remap in the webhook controller). ``status_code`` is
    caller-supplied (401 for bad signatures, 404 for platforms without direct
    ingress).
    """

    def __init__(self, detail: str, *, status_code: int = 401):
        super().__init__(
            detail, code="SURFACE_WEBHOOK_AUTH_FAILED", status_code=status_code
        )


class SlackSignInError(AgentSurfaceError):
    """Sign in with Slack could not prove who this person is in the workspace.

    ``reason`` is a fixed token the web page turns into words, so nothing a
    caller sent is ever echoed back.
    """

    REASONS = frozenset(
        {
            "expired",
            "session_changed",
            "other_slack_user",
            "not_in_organization",
            "not_installed",
            "unavailable",
            "cancelled",
        }
    )

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason if reason in self.REASONS else "unavailable"
        super().__init__(message=message, code="SLACK_SIGN_IN_REFUSED", status_code=422)
