"""Files to and from Slack: downloading what a person attached, uploading ours.

Split out of :class:`SlackPlatformService` by use case. Moving bytes is its own
job -- capped streams, upload tickets, a second completion call when a
workspace refuses the customized identity -- and none of it is about how a
message is worded or posted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.agent_surfaces.domain.reply_targets import (
    SlackReplyTarget,
    reply_target_as,
)

from app.modules.agent_surfaces.domain.credentials import SlackCredentials

import mimetypes

from app.modules.agent_surfaces.platforms.slack.errors import slack_api_error
from app.modules.agent_surfaces.platforms.outbound_http import outbound_http

from app.core.net.capped_read import read_capped
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
)
from app.modules.agent_surfaces.platforms.common import payload_first, payload_text
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    slack_access_token,
    slack_customized_message_kwargs,
)

if TYPE_CHECKING:
    from slack_sdk.errors import SlackApiError


class SlackFilesMixin:
    """Attachment download and file upload for :class:`SlackPlatformService`."""

    credentials: SlackCredentials

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, object],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound Slack attachment (no RunContext)."""
        del event
        token = slack_access_token(self.credentials)
        if not token:
            return None
        download_url, file_item = await self._resolve_attachment_url(attachment)
        if not download_url:
            return None
        file_name = self._attachment_file_name(attachment, file_item, download_url)
        content = await self._download_capped(download_url, token)
        return (
            content,
            file_name,
            _attachment_mime_type(attachment, file_item, file_name),
        )

    async def _resolve_attachment_url(
        self, attachment: dict[str, object]
    ) -> tuple[str, dict[str, object]]:
        """The URL to fetch, asking Slack for it when the event carried none."""
        download_url = payload_text(attachment, "download_url").strip()
        if download_url:
            return download_url, {}
        file_id = payload_text(attachment, "id").strip()
        if not file_id:
            return "", {}
        client = await build_slack_client(self.credentials)
        response = await client.files_info(file=file_id)
        file_item = response.get("file") or {}
        return (
            payload_first(file_item, "url_private_download", "url_private").strip(),
            file_item,
        )

    def _attachment_file_name(
        self,
        attachment: dict[str, object],
        file_item: dict[str, object],
        download_url: str,
    ) -> str:
        """A name for the file: from the event, from Slack, or from the URL."""
        return (
            payload_text(attachment, "name").strip()
            or payload_text(file_item, "name").strip()
            or self._filename_from_url(download_url)
            or "slack_file"
        )

    async def _download_capped(self, url: str, token: str) -> bytes:
        """Fetch the file, streamed and capped.

        The caller checked ``attachment["size"]`` first, but Slack does not
        always send one, and a declared size is the sender's claim rather than a
        limit on what actually arrives.
        """
        async with outbound_http(60.0) as http_client:
            async with http_client.stream(
                "GET",
                url,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                response.raise_for_status()
                return await read_capped(
                    response.aiter_bytes(), max_bytes=INBOUND_ATTACHMENT_BYTE_CAP
                )

    async def send_file_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        *,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        """Upload + share raw file bytes to the inbound channel (egress)."""
        del mime_type  # Slack infers the type from the filename.
        token = slack_access_token(self.credentials)
        channel = reply_target_as(event, SlackReplyTarget).channel
        if not token or not channel:
            return False
        thread_ts = reply_target_as(event, SlackReplyTarget).thread_ts
        client = await build_slack_client(self.credentials)
        upload_ticket = await client.files_getUploadURLExternal(
            filename=file_name, length=len(file_bytes)
        )
        upload_url = str(upload_ticket["upload_url"])
        file_id = str(upload_ticket["file_id"])
        async with outbound_http(60.0) as http_client:
            upload_response = await http_client.post(
                upload_url, files={"file": (file_name, file_bytes)}
            )
            upload_response.raise_for_status()

        completion_payload: dict[str, object] = {
            "files": [{"id": file_id, "title": caption or file_name}],
            "channel_id": channel,
        }
        if caption:
            completion_payload["initial_comment"] = caption
        if thread_ts:
            completion_payload["thread_ts"] = thread_ts
        completion_payload.update(
            slack_customized_message_kwargs(self.credentials, None)
        )
        try:
            await client.files_completeUploadExternal(**completion_payload)
        except slack_api_error() as exc:
            if not _slack_rejected_customized_identity(exc):
                raise
            fallback_payload: dict[str, object] = {
                "files": completion_payload["files"],
                "channel_id": channel,
            }
            if thread_ts:
                fallback_payload["thread_ts"] = thread_ts
            await client.files_completeUploadExternal(**fallback_payload)
        return True

    def _filename_from_url(self, value: str) -> str:
        return str(value or "").rstrip("/").split("/")[-1].strip()


def _slack_rejected_customized_identity(exc: SlackApiError) -> bool:
    error_code = str((exc.response or {}).get("error") or "")
    if error_code in {"invalid_arguments", "invalid_arg_name"}:
        return True
    messages = (exc.response or {}).get("response_metadata", {}).get("messages") or []
    return any("username" in str(message).lower() for message in messages)


def _attachment_mime_type(
    attachment: dict[str, object], file_item: dict[str, object], file_name: str
) -> str:
    """The content type: from the event, from Slack, or guessed from the name."""
    return (
        payload_first(attachment, "mime_type", "content_type").strip()
        or payload_text(file_item, "mimetype").strip()
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )
