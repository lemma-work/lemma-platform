"""What the Telegram service builds to send: media uploads and message text.

Split from :mod:`service`, which owns the client and the verbs, because it is the
part with no state in it -- how a file becomes an upload, how a resource becomes
a message body -- and the part that grew when uploads moved onto the shared
client.
"""

from __future__ import annotations

from html import escape
from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
)
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy, with_retry
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
    classify_telegram_error,
    telegram_retry_after,
)

logger = get_logger(__name__)

# Uploads are the one call whose duration scales with the payload (up to tens of
# megabytes), so they get more than the client's 10 s JSON-round-trip default.
_UPLOAD_TIMEOUT_SECONDS = 60.0

# Telegram rejects a media caption over 1024 characters outright, which lost the
# file along with the words.
_CAPTION_LIMIT = 1024


async def upload(
    client: TelegramClient,
    policy: RetryPolicy,
    method_name: str,
    file_field: str,
    *,
    data: dict[str, Any],
    file_name: str,
    content: bytes,
    mime_type: str,
) -> None:
    """One upload through the shared client, with its guard, retry and timeout.

    These went straight to ``httpx`` with the URL built by hand, which skipped
    ``assert_safe_api_base`` (a tenant-supplied ``api_base_url`` is exactly what
    that guard exists for) and the retry every text send gets.
    """
    await with_retry(
        lambda: client.call_multipart(
            method_name,
            fields=data,
            files={file_field: (file_name, content, mime_type)},
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        ),
        policy=policy,
        classify=classify_telegram_error,
        retry_after=telegram_retry_after,
    )


async def send_file(
    client: TelegramClient,
    policy: RetryPolicy,
    *,
    data: dict[str, Any],
    file_name: str,
    content: bytes,
    mime_type: str,
) -> None:
    """Send a file as the media kind its type suggests, else as a document.

    Photo, audio and video each reject things a document takes -- a photo over
    10 MB or with extreme dimensions, audio that is not mp3/m4a. A 400 is
    Telegram saying "not as that", so the same bytes are tried as a document once
    before the caller degrades the file to a link the recipient may not be able
    to open.
    """
    method_name, file_field = method_for_send_type(resolve_send_type(mime_type))
    try:
        await upload(
            client,
            policy,
            method_name,
            file_field,
            data=data,
            file_name=file_name,
            content=content,
            mime_type=mime_type,
        )
    except TelegramApiError as exc:
        if method_name == "sendDocument" or exc.status_code != 400:
            raise
        logger.warning(
            "agent_surfaces.telegram.media_type_rejected.degraded",
            method=method_name,
            mime_type=mime_type,
            exc_info=True,
        )
        await upload(
            client,
            policy,
            "sendDocument",
            "document",
            data=data,
            file_name=file_name,
            content=content,
            mime_type=mime_type,
        )


def resolve_send_type(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "photo"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "document"


def method_for_send_type(send_type: str) -> tuple[str, str]:
    normalized = str(send_type).lower()
    if normalized == "photo":
        return "sendPhoto", "photo"
    if normalized == "audio":
        return "sendAudio", "audio"
    if normalized == "video":
        return "sendVideo", "video"
    return "sendDocument", "document"


def caption(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= _CAPTION_LIMIT:
        return text
    return text[: _CAPTION_LIMIT - 1].rstrip() + "…"


def question_with_option_notes(question: SurfaceQuestion) -> str:
    """The question, then a line for each option that explains itself.

    A button carries a label and nothing else, so an option's description used
    to be dropped -- the person chose between "Refund" and "Credit" without the
    sentence the agent wrote to tell them apart. It travels in the message.
    """
    notes = [
        f"• {option.label} — {option.description}"
        for option in question.options
        if option.description
    ]
    if not notes:
        return question.question
    return f"{question.question}\n\n" + "\n".join(notes)


def display_resource_text(render_plan: SurfaceDisplayRenderPlan) -> str:
    parts = [f"<b>{escape(render_plan.title)}</b>"]
    if render_plan.summary:
        parts.append(escape(render_plan.summary))
    for line in render_plan.detail_lines[:5]:
        parts.append(f"<blockquote>{escape(line)}</blockquote>")
    if render_plan.preview_block:
        parts.append(f"<pre>{escape(render_plan.preview_block)}</pre>")
    action = render_plan.primary_action
    if action is not None:
        parts.append(
            f'<a href="{escape(action.url, quote=True)}">{escape(action.label)}</a>'
        )
    return "\n\n".join(parts)


def button_text(value: str) -> str:
    text = " ".join(str(value or "").split()) or "Open"
    return text if len(text) <= 64 else text[:63].rstrip() + "..."
