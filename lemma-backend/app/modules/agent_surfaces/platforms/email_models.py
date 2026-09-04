from __future__ import annotations

from app.modules.agent_surfaces.platforms.common import SurfaceFileAttachment


class EmailFileAttachment(SurfaceFileAttachment):
    message_id: str | None = None
    content_bytes_base64: str | None = None
    is_inline: bool = False
