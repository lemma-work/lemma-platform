from __future__ import annotations

from pydantic import BaseModel

from app.modules.agent_surfaces.platforms.common import SurfaceFileAttachment


class WhatsAppToolResult(BaseModel):
    success: bool = False
    error: str | None = None
    message: str | None = None
    guidance: str | None = None


class WhatsAppFileAttachment(SurfaceFileAttachment):
    pass
