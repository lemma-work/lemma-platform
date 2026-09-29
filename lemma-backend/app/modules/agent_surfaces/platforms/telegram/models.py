from __future__ import annotations

from pydantic import BaseModel

from app.modules.agent_surfaces.platforms.common import SurfaceFileAttachment


class TelegramToolResult(BaseModel):
    success: bool = False
    error: str | None = None
    message: str | None = None


class TelegramFileAttachment(SurfaceFileAttachment):
    file_id: str | None = None
