"""Email sender service for sending emails via SMTP."""

from __future__ import annotations

import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from pathlib import Path
from typing import Optional
import logging
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(Exception):
    """Raised when SMTP email is not properly configured."""

    pass


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
        self.transport = transport
        self.output_dir = output_dir

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

        if not settings.is_email_configured():
            raise EmailNotConfiguredError(
                "SMTP email is not configured. Please set the following environment variables: "
                "SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL"
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
                output_dir = Path(self.output_dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                safe_email = to_email.replace("@", "_at_").replace("/", "_")
                email_file = output_dir / f"{timestamp}_{safe_email}.json"
                email_file.write_text(
                    json.dumps(
                        {
                            "to_email": to_email,
                            "from_email": self.from_email,
                            "from_name": self.from_name,
                            "subject": subject,
                            "text_content": text_content,
                            "html_content": html_content,
                            "transport": self.transport,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                logger.info(f"Email written to file {email_file}")
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
            async with aiosmtplib.SMTP(
                hostname=self.smtp_host,
                port=self.smtp_port,
                use_tls=self.use_tls,
            ) as server:
                await server.login(self.smtp_user, self.smtp_password)
                await server.send_message(msg)

            logger.info(f"Email sent successfully to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False
