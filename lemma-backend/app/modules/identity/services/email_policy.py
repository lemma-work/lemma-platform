from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from anyio import to_thread
from email_validator import (
    EmailNotValidError,
    EmailSyntaxError,
    EmailUndeliverableError,
    validate_email,
)
from sqlalchemy import func, select

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.models.user_models import User


_DISPOSABLE_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "disposable_email_domains.txt"
)


@dataclass(frozen=True)
class EmailPolicyRejection:
    reason: str
    evidence_source: str
    permanent: bool = True


class EmailPolicyError(ValueError):
    def __init__(self, rejection: EmailPolicyRejection):
        super().__init__(rejection.reason)
        self.rejection = rejection


@lru_cache(maxsize=1)
def _disposable_domains() -> frozenset[str]:
    try:
        return frozenset(
            line.strip().lower()
            for line in _DISPOSABLE_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )
    except FileNotFoundError:
        return frozenset()


async def _validate_with_dns(email: str):
    return await to_thread.run_sync(
        lambda: validate_email(email, check_deliverability=True)
    )


def _dns_rejection(exc: EmailUndeliverableError) -> EmailPolicyRejection | None:
    """Classify only deterministic DNS evidence as account-deactivation safe."""
    message = str(exc).lower()
    if "does not exist" in message:
        return EmailPolicyRejection("INVALID_DOMAIN", "dns")
    if "does not accept email" in message and exc.__cause__ is None:
        # email-validator raises this directly only for an explicit RFC 7505
        # null MX. Its no-MX/A/AAAA path chains the underlying DNS exception.
        return EmailPolicyRejection("NULL_MX", "dns")
    if "error while checking" in message:
        # Resolver and library failures are unknown deliverability, not evidence
        # that the mailbox is permanently invalid.
        return None
    return EmailPolicyRejection(
        "UNDELIVERABLE_DOMAIN",
        "dns",
        permanent=False,
    )


async def validate_auth_email(email: str) -> str:
    """Normalize and validate an auth email without probing its SMTP recipient."""
    try:
        syntax_result = validate_email(str(email).strip(), check_deliverability=False)
    except EmailSyntaxError as exc:
        raise EmailPolicyError(
            EmailPolicyRejection("INVALID_SYNTAX", "email-validator")
        ) from exc
    except EmailNotValidError as exc:
        raise EmailPolicyError(
            EmailPolicyRejection("INVALID_EMAIL", "email-validator")
        ) from exc

    normalized = normalize_identity_email(syntax_result.normalized)
    domain = normalized.rsplit("@", 1)[1]
    allowlist = {
        item.strip().lower() for item in settings.auth_disposable_email_allowlist
    }
    if (
        settings.auth_disposable_email_domains_enabled
        and domain not in allowlist
        and domain in _disposable_domains()
    ):
        raise EmailPolicyError(
            EmailPolicyRejection("DISPOSABLE_DOMAIN", "oss-domain-list")
        )

    if settings.auth_email_deliverability_checks_enabled:
        try:
            deliverable = await _validate_with_dns(normalized)
            normalized = normalize_identity_email(deliverable.normalized)
        except EmailUndeliverableError as exc:
            rejection = _dns_rejection(exc)
            if rejection is not None:
                raise EmailPolicyError(rejection) from exc
        except EmailSyntaxError as exc:
            raise EmailPolicyError(
                EmailPolicyRejection("INVALID_SYNTAX", "email-validator")
            ) from exc
        except EmailNotValidError, TimeoutError, OSError:
            # The library treats resolver timeouts and temporary DNS failures as
            # unknown deliverability. Operational lookup failures are likewise
            # allowed here instead of deactivating a real account.
            pass

    return normalized


async def validate_outbound_email(email: str) -> str:
    """Validate an address and refuse mail to a deactivated local account."""
    normalized = await validate_auth_email(email)
    async with async_session_maker() as session:
        user = await session.scalar(
            select(User).where(func.lower(User.email) == normalized)
        )
    if user is not None and (not user.is_active or user.is_deleted):
        raise EmailPolicyError(
            EmailPolicyRejection("ACCOUNT_INACTIVE", "local-user-state")
        )
    return normalized
