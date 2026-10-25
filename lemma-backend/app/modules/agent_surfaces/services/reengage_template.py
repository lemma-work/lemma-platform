"""Which template asks a member to reply, on a surface whose window closed.

The surface's own wins. A surface on the deployment's shared WhatsApp number
-- no connected account of its own -- falls back to the deployment's template,
because that number's business account is the deployment's, and only a
template approved there can be sent from it. A surface on its own account
has no fallback: the deployment's template was never approved on it.
"""

from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.surface_config import (
    ReengageTemplate,
    SurfaceWhatsAppConfig,
)


def reengage_template_for(surface: AgentSurfaceEntity) -> ReengageTemplate | None:
    own = surface.config.whatsapp.template()
    if own is not None or surface.pod_connector_slug is not None:
        return own
    return SurfaceWhatsAppConfig(
        reengage_template=surface_settings.whatsapp_reengage_template,
        language=surface_settings.whatsapp_reengage_template_language,
    ).template()


__all__ = ["reengage_template_for"]
