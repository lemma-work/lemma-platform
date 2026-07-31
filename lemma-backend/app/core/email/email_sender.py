"""Email sender service for sending emails via SMTP."""

from __future__ import annotations

import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import os
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from app.core.config import reveal_secret, settings
from app.core.log.log import get_logger

logger = get_logger(__name__)


class EmailNotConfiguredError(Exception):
    """Raised when SMTP email is not properly configured."""


class EmailDeliveryError(Exception):
    """Raised when a security-sensitive email could not be delivered to SMTP."""


class EmailSender:
    """Service for sending emails via SMTP."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        from_email: str,
        from_name: str = "Lemma",
        use_tls: bool = True,
        transport: str = "smtp",
        output_dir: str = "/tmp/lemma-emails",
    ):
        """
        Initialize email sender.

        Args:
            smtp_host: SMTP server hostname
            smtp_port: SMTP server port
            smtp_user: SMTP username
            smtp_password: SMTP password
            from_email: From email address
            from_name: From name
            use_tls: Whether to use TLS
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.from_email = from_email
        self.from_name = from_name
        self.use_tls = use_tls
        if transport == "filesystem" and settings.environment == "production":
            raise EmailNotConfiguredError(
                "Filesystem email transport is not permitted in production"
            )
        self.transport = transport
        self.output_dir = output_dir

    def _write_filesystem_email(
        self,
        *,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str],
    ) -> None:
        """Write a local/test mail spool with owner-only filesystem access."""
        output_dir = Path(self.output_dir)
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        output_dir.chmod(0o700)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_email = to_email.replace("@", "_at_").replace("/", "_")
        email_file = output_dir / f"{timestamp}_{safe_email}.json"
        descriptor = os.open(
            email_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            # This opt-in local/test spool intentionally contains reset and
            # verification links so realistic auth flows can be exercised. It
            # is forbidden in production and confined to 0700/0600 paths.
            json.dump(  # lgtm[py/clear-text-storage-sensitive-data]
                {
                    "to_email": to_email,
                    "from_email": self.from_email,
                    "from_name": self.from_name,
                    "subject": subject,
                    "text_content": text_content,
                    "html_content": html_content,
                    "transport": self.transport,
                },
                output,
                indent=2,
            )

    @classmethod
    def from_settings(cls) -> EmailSender:
        """
        Create an email sender instance from application settings.

        Returns:
            EmailSender instance

        Raises:
            EmailNotConfiguredError: If SMTP is not properly configured
        """
        if settings.email_transport == "filesystem":
            return cls(
                smtp_host=settings.smtp_host,
                smtp_port=settings.smtp_port,
                smtp_user=settings.smtp_user or "",
                smtp_password=settings.smtp_password or "",
                from_email=settings.smtp_from_email or "hello@updates.lemma.work",
                from_name=settings.smtp_from_name,
                use_tls=settings.smtp_use_tls,
                transport="filesystem",
                output_dir=settings.email_output_dir,
            )

        resend_api_key = reveal_secret(settings.resend_api_key)
        explicit_smtp = all(
            (
                settings.smtp_host,
                settings.smtp_user,
                settings.smtp_password,
                settings.smtp_from_email,
            )
        )
        if not explicit_smtp and resend_api_key:
            return cls(
                smtp_host="smtp.resend.com",
                smtp_port=465,
                smtp_user="resend",
                smtp_password=resend_api_key,
                from_email=settings.resend_from_email,
                from_name=settings.smtp_from_name,
                use_tls=True,
                transport="smtp",
                output_dir=settings.email_output_dir,
            )

        if not settings.is_email_configured():
            raise EmailNotConfiguredError(
                "Email is not configured. Set RESEND_API_KEY, or set SMTP_HOST, "
                "SMTP_USER, SMTP_PASSWORD, and SMTP_FROM_EMAIL."
            )

        return cls(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user,  # type: ignore
            smtp_password=settings.smtp_password,  # type: ignore
            from_email=settings.smtp_from_email,  # type: ignore
            from_name=settings.smtp_from_name,
            use_tls=settings.smtp_use_tls,
            transport="smtp",
            output_dir=settings.email_output_dir,
        )

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        *,
        raise_on_failure: bool = False,
    ) -> bool:
        """
        Send an email asynchronously.

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_content: HTML email content
            text_content: Plain text email content (optional)

        Returns:
            True if email was sent successfully, False otherwise
        """
        try:
            if self.transport == "filesystem":
                self._write_filesystem_email(
                    to_email=to_email,
                    subject=subject,
                    html_content=html_content,
                    text_content=text_content,
                )
                return True

            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email

            # Add text part if provided
            if text_content:
                text_part = MIMEText(text_content, "plain")
                msg.attach(text_part)

            # Add HTML part
            html_part = MIMEText(html_content, "html")
            msg.attach(html_part)

            # Send email asynchronously
            implicit_tls = self.use_tls and self.smtp_port == 465
            async with aiosmtplib.SMTP(
                hostname=self.smtp_host,
                port=self.smtp_port,
                use_tls=implicit_tls,
                start_tls=self.use_tls and not implicit_tls,
                timeout=15,
            ) as server:
                await server.login(self.smtp_user, self.smtp_password)
                await server.send_message(msg)

            return True

        except Exception as exc:
            logger.error("email.send.failed", exc_info=True)
            if raise_on_failure:
                raise EmailDeliveryError("SMTP delivery failed") from exc
            return False
