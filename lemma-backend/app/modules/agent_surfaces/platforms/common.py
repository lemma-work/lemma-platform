from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    has_shared_system_bot,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)

# Hosts that are not publicly reachable for inbound webhook delivery.
_LOCAL_WEBHOOK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})

# Platforms that receive inbound events on the shared platform-level webhook.
_PLATFORM_WEBHOOK_TYPES = frozenset(
    {
        SurfacePlatform.SLACK,
        SurfacePlatform.TELEGRAM,
        SurfacePlatform.WHATSAPP,
        SurfacePlatform.TEAMS,
    }
)


class UnsafeApiBaseError(Exception):
    """A surface's API base URL points somewhere we will not send credentials."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


async def assert_safe_api_base(api_base: str, *, platform: str) -> str:
    """Refuse a surface API base that is not on the public internet.

    ``api_base_url`` is a real feature — a self-hosted Telegram Bot API server,
    a sovereign-cloud Graph or Slack endpoint — so it stays. What it must not be
    is unguarded: it arrives from stored account credentials
    (``credential_resolver._CONTEXT_KEYS``), which makes it tenant-supplied
    input, and every platform client dialled it with a bare ``httpx`` client
    that asks no questions. Pointed at ``169.254.169.254`` it would fetch the
    instance's own credentials and hand them back through the surface; pointed
    at an internal host it walks the cluster.

    The connector kinds already re-check their targets this way. This is the
    same check, at the same moment — the point of use — reusing the same guard
    so there is one definition of "not the public internet" in the product.
    """
    from app.core.net.url_guard import UnsafeUrlError, assert_safe_url

    try:
        await assert_safe_url(api_base)
    except UnsafeUrlError as exc:
        raise UnsafeApiBaseError(
            f"Refusing to call the {platform} API at an unsafe address: {exc}",
            reason=exc.reason,
        ) from exc
    return api_base


def public_https_api_url_available() -> bool:
    """True when ``settings.api_url`` is a public HTTPS URL.

    External platforms can only deliver webhooks to a publicly reachable HTTPS
    callback; localhost/http values are only usable with native polling/socket
    modes. Shared by surface creation and the setup-status controller.
    """
    parsed = urlparse(settings.api_url.rstrip("/"))
    hostname = parsed.hostname or ""
    return parsed.scheme == "https" and hostname.lower() not in _LOCAL_WEBHOOK_HOSTS


def receives_without_public_link(platform: SurfacePlatform) -> bool:
    """True when this runtime can receive on ``platform`` with no public URL.

    Telegram polling, Slack Socket Mode and Resend polling each pull events
    rather than waiting for a webhook, so a desktop or LAN runtime can run those
    surfaces. Everything else -- WhatsApp and Teams always -- needs somewhere on
    the internet for the platform to deliver to.

    Shared by the write path's refusal and the catalog, so the catalog never
    offers what the write path will refuse.
    """
    if platform is SurfacePlatform.TELEGRAM:
        return surface_settings.enable_telegram_polling_mode
    if platform is SurfacePlatform.SLACK:
        return surface_settings.enable_slack_socket_mode
    if platform is SurfacePlatform.RESEND:
        return surface_settings.enable_resend_polling_mode
    return False


def platform_webhook_url(platform: SurfacePlatform) -> str | None:
    """The one URL every surface of a platform receives events on.

    Slack's manifest uses this rather than a per-surface URL. Which signing
    secret verifies a request is decided from the workspace in the payload, so
    one endpoint serves the deployment's app and every org's own app alike —
    and the manifest stops depending on a surface existing first.
    """
    if not public_https_api_url_available():
        return None
    return f"{settings.api_url.rstrip('/')}/surfaces/webhooks/{platform.value.lower()}"


def computed_webhook_url(surface: AgentSurfaceEntity) -> str | None:
    """The inbound webhook URL for a surface, or None when not webhook-driven.

    Shared by the surface response builder and the unified setup read. Returns
    None unless the surface uses WEBHOOK event mode and the API is publicly
    reachable. Telegram and WhatsApp with a connected account get a
    surface-specific URL (each account has its own webhook secret/verify
    token); a WhatsApp surface holding a pooled number gets that number's own;
    the other platform webhooks share a platform-level URL.
    """
    if not public_https_api_url_available():
        return None
    base = settings.api_url.rstrip("/")
    if has_shared_system_bot(surface.surface_type) and surface.account_id is not None:
        return f"{base}/surfaces/{surface.id}/webhook"
    # A pooled number receives on a callback of its own, because the handshake
    # carries nothing that could select a token on the shared URL -- so the path
    # is the identifier. The number is the one this surface holds, which is what
    # `scripts/whatsapp_numbers.py` tells the operator to register with Meta.
    # Returning the shared URL here published a different address than the one
    # the pool's own GET route answers on, and an operator who pasted it failed
    # the handshake against a token that was never checked.
    if surface.surface_type is SurfacePlatform.WHATSAPP and surface.surface_identity_id:
        return (
            f"{base}/surfaces/webhooks/whatsapp/numbers/{surface.surface_identity_id}"
        )
    # Slack is deliberately absent here: a surface running the org's own app
    # receives on the same shared endpoint as everyone else, because the secret
    # that verifies a request is chosen from the workspace in the payload
    # rather than from the URL it arrived on.
    if surface.surface_type in _PLATFORM_WEBHOOK_TYPES:
        return f"{base}/surfaces/webhooks/{surface.surface_type.value.lower()}"
    return None


class SurfaceFileAttachment(BaseModel):
    id: str | None = None
    name: str | None = None
    download_url: str | None = None
    permalink: str | None = None
    content_type: str = ""
    file_type: str = ""
    mime_type: str | None = None
    size: int | None = None

    def detail_label(self) -> str:
        return (
            self.content_type.strip()
            or (self.mime_type or "").strip()
            or self.file_type.strip()
        )


AttachmentT = TypeVar("AttachmentT", bound=SurfaceFileAttachment)


def coerce_attachments(
    attachments: Iterable[Any],
    model_cls: type[AttachmentT],
) -> list[AttachmentT]:
    """Normalize metadata attachments (models or dicts) into the platform model."""
    normalized: list[AttachmentT] = []
    for attachment in attachments:
        if isinstance(attachment, model_cls):
            normalized.append(attachment)
        elif hasattr(attachment, "model_dump"):
            normalized.append(
                model_cls.model_validate(attachment.model_dump(mode="json"))
            )
        else:
            normalized.append(model_cls.model_validate(attachment))
    return normalized


def channel_author_label(
    display_name: str | None,
    user_id: str | None = None,
) -> str | None:
    """Per-message author attribution for background channel messages."""
    who = (display_name or "").strip() or (user_id or "").strip()
    if not who:
        return None
    return f"{who} (other participant)"


def render_attachment_prompt_block(
    attachments: Iterable[SurfaceFileAttachment | dict[str, Any]],
    *,
    platform: str,
) -> str:
    normalized = _normalize_attachments(attachments)
    if not normalized:
        return ""

    platform_name = str(platform or "").upper() or "external"
    lines = [f"Files attached to this {platform_name.title()} message:"]
    for attachment in normalized[:10]:
        label = attachment.name or "unnamed file"
        details = [label]
        detail_label = attachment.detail_label()
        if detail_label:
            details.append(detail_label)
        if attachment.size is not None:
            details.append(f"{attachment.size} bytes")
        line = "- " + " | ".join(details)
        if attachment.id:
            line += f" | id={attachment.id}"
        if attachment.download_url:
            line += f" | download_url={attachment.download_url}"
        elif attachment.permalink:
            line += f" | permalink={attachment.permalink}"
        lines.append(line)
    return "\n".join(lines)


def render_attachment_summary_suffix(
    attachments: Iterable[SurfaceFileAttachment | dict[str, Any]],
) -> str:
    normalized = _normalize_attachments(attachments)
    if not normalized:
        return ""

    parts: list[str] = []
    for attachment in normalized[:3]:
        detail = attachment.name or "unnamed file"
        detail_label = attachment.detail_label()
        if detail_label:
            detail += f" ({detail_label})"
        if attachment.id:
            detail += f" id={attachment.id}"
        if attachment.download_url:
            detail += f" download_url={attachment.download_url}"
        parts.append(detail)

    if not parts:
        return ""
    return " | files: " + "; ".join(parts)


def _normalize_attachments(
    attachments: Iterable[SurfaceFileAttachment | dict[str, Any]],
) -> list[SurfaceFileAttachment]:
    normalized: list[SurfaceFileAttachment] = []
    for raw in attachments:
        if isinstance(raw, SurfaceFileAttachment):
            attachment = raw
        elif isinstance(raw, dict):
            try:
                attachment = SurfaceFileAttachment.model_validate(raw)
            except ValidationError:
                continue
        else:
            continue
        if not attachment.name and not attachment.id and not attachment.download_url:
            continue
        normalized.append(attachment)
    return normalized


# A provider refusing this credential. Retrying cannot change the answer: no
# number of attempts turns a send-only API key into one that may read inbound
# mail, or an expired token into a live one. 429 and 5xx are deliberately not
# here — those *do* change on their own.
UNRETRYABLE_PROVIDER_STATUS = frozenset({401, 403})


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """Safe, structured facts about a failed provider call.

    A value rather than a dict because the logging contract forbids ``**kwargs``
    at a log call — every field has to be named where it is emitted, so the
    static checker can hold the call to the catalog. Splatting a dict would have
    hidden these fields from exactly the gate that keeps logs honest.
    """

    failure_type: str
    status_code: int | None = None
    provider_error: str | None = None


def payload_text(source: Any, key: str) -> str:
    """A webhook field as a string, with absent, null and empty all reading as "".

    Every parser digs strings out of a payload it does not control, so every
    read carries the same `or ""` to survive a missing key or an explicit null.
    Ninety-five of them across six parsers, and each one counted as a branch --
    which is most of why the parsers measured as the most complex code in the
    module while doing nothing more complicated than reading a dictionary.
    """
    if not source:
        return ""
    return str(source.get(key) or "")


def payload_first(source: Any, *keys: str) -> str:
    """The first of these keys that carries a value, as a string.

    Providers spell the same field several ways -- `conversation_id`,
    `conversationId`, `id` -- and a parser has to try each. Written inline that
    is a chain of `or`s, and every link counted as a branch. The loop is here
    once instead.
    """
    for key in keys:
        value = payload_text(source, key)
        if value:
            return value
    return ""


def payload_any(source: Any, *keys: str) -> Any:
    """The first of these keys with a value, left as it arrived.

    `payload_first` for values that are not text -- attachment bytes, sizes,
    nested objects -- where stringifying would be wrong. Same reason it exists:
    providers spell one field several ways, and the chain of `or`s that tries
    each counted as a branch per spelling.
    """
    if not source:
        return None
    for key in keys:
        value = source.get(key)
        if value:
            return value
    return None


def payload_section(source: Any, key: str) -> dict[str, Any]:
    """A nested object from a payload, or an empty one to keep reading from."""
    if not source:
        return {}
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def provider_failure(exc: Exception) -> ProviderFailure:
    """What went wrong, in terms safe to write to a log.

    Never ``str(exc)``. The logging pipeline strips any field named ``error``
    outright, because exception text can carry keys and personal data — so the
    one line explaining a production failure arrives with the explanation
    removed. A Resend key restricted to sending presented for hours as an
    unexplained "enrichment failed", while the provider had been answering
    ``restricted_api_key`` the whole time.

    The HTTP status and the provider's own machine-readable error name are
    bounded, carry no secrets, and usually name the fix by themselves.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return ProviderFailure(failure_type=type(exc).__name__)
    try:
        body = response.json()
    except ValueError:
        # An HTML error page or an empty body. Not itself a failure — the status
        # is the useful half, and it has already been captured.
        body = None
    name = body.get("name") if isinstance(body, dict) else None
    return ProviderFailure(
        failure_type=type(exc).__name__,
        status_code=status,
        provider_error=name if isinstance(name, str) and name else None,
    )


def text_or_none(value: Any) -> str | None:
    """A value as trimmed text, or None when it is absent or blank.

    The `or None` half of the family. `payload_text` answers "" for a missing
    field because a parser usually wants to keep reading; a field on its way
    into a record wants the absence kept, and spelling that out per field is
    three branches each.
    """
    if value is None:
        return None
    return str(value).strip() or None


# Exceptions a delivery is willing to degrade on: the platform said no, or the
# network did. Enumerated rather than caught as `Exception`, and the difference
# matters — a TypeError from a signature that drifted, or an AttributeError from
# a method a platform never grew, is a bug in this codebase and must crash
# loudly. Swallowing exactly that class of thing is how `stream_progress` was
# silently dead on two platforms for a release (see `test_adapter_contract`).
#
# Import errors are tolerated so a deployment without an optional SDK still
# loads; a platform whose SDK is missing cannot be reached anyway.
def _platform_transport_errors() -> tuple[type[BaseException], ...]:
    import httpx

    errors: list[type[BaseException]] = [
        httpx.HTTPError,
        httpx.InvalidURL,
        TimeoutError,
        ConnectionError,
        OSError,
    ]
    try:
        import aiohttp

        errors.append(aiohttp.ClientError)
    except ImportError:  # pragma: no cover - aiohttp ships with the backend
        pass
    try:
        from slack_sdk.errors import SlackApiError, SlackClientError

        errors.extend((SlackApiError, SlackClientError))
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.modules.agent_surfaces.platforms.telegram.client import (
            TelegramApiError,
        )

        errors.append(TelegramApiError)
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.modules.agent_surfaces.platforms.whatsapp.client import (
            WhatsAppApiError,
        )

        errors.append(WhatsAppApiError)
    except ImportError:  # pragma: no cover
        pass
    from app.modules.agent_surfaces.domain.errors import AgentSurfaceError

    errors.append(AgentSurfaceError)
    return tuple(errors)


PLATFORM_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    _platform_transport_errors()
)
