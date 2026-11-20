"""Which of a pod's web widgets a page on one origin may use.

A hosted app embeds its pod's chat and forms through a web widget, and needs
the widget's public key to do it. The key is not a credential -- it is written
into every page that embeds the widget (``domain/web_widgets``) -- so the app
host hands it over in ``window.__LEMMA_CONFIG__`` rather than making every link
to the app carry it.

Only widgets the page could use anyway: switched on, and allowing the page's
origin. A key the widget would refuse that page is no use to it, and listing
every widget would tell a page about the pod's other sites. Nothing at all
while public web is switched off, when no widget answers anybody.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.public_web import public_web_enabled
from app.core.runtime_config import PageWebWidget
from app.modules.agent_surfaces.domain.web_widgets import WidgetAnswer
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)


def widget_script_url() -> str:
    """Where ``widget.js`` is served: the API host, which every call it makes
    is derived from."""
    return f"{str(settings.api_url).rstrip('/')}/public/web/widget.js"


async def widgets_for_page(
    session: AsyncSession, *, pod_id: UUID, origin: str
) -> tuple[PageWebWidget, ...]:
    """The pod's widgets a page on ``origin`` may use, oldest first."""
    if not public_web_enabled():
        return ()
    widgets = await WebWidgetRepository(session).list(pod_id=pod_id)
    script_url = widget_script_url()
    return tuple(
        PageWebWidget(
            name=widget.name, public_key=widget.public_key, script_url=script_url
        )
        for widget in widgets
        if widget.answer is not WidgetAnswer.OFF and widget.allows_origin(origin)
    )
