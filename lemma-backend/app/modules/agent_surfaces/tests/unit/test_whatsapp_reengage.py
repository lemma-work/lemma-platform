"""Which template reopens a closed WhatsApp window, and what it looks like.

The journey -- refused without a template, asked and held with one, delivered
on the reply -- is `tests/e2e/test_whatsapp_reengage_e2e.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_config import (
    ReengageTemplate,
    SurfaceWhatsAppConfig,
)
from app.modules.agent_surfaces.domain.surface_representation import (
    SurfaceWhatsAppConfigInput,
)
from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppClient
from app.modules.agent_surfaces.services.reengage_template import (
    reengage_template_for,
)

pytestmark = pytest.mark.asyncio


def _surface(*, template: str | None, account: bool) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="whatsapp",
        surface_type=SurfacePlatform.WHATSAPP,
        pod_connector_slug="own-bot" if account else None,
        config=SurfaceConfig(
            whatsapp=SurfaceWhatsAppConfig(reengage_template=template, language="de")
        ),
    )


async def test_the_surfaces_own_template_wins(monkeypatch):
    monkeypatch.setattr(surface_settings, "whatsapp_reengage_template", "shared")

    assert reengage_template_for(
        _surface(template="own_one", account=False)
    ) == ReengageTemplate(name="own_one", language="de")


async def test_the_shared_number_falls_back_to_the_deployments_template(monkeypatch):
    monkeypatch.setattr(surface_settings, "whatsapp_reengage_template", "shared")

    template = reengage_template_for(_surface(template=None, account=False))

    assert template is not None and template.name == "shared"


async def test_a_surface_on_its_own_account_never_borrows_the_deployments(
    monkeypatch,
):
    """The deployment's template is approved on its account, not this one."""
    monkeypatch.setattr(surface_settings, "whatsapp_reengage_template", "shared")

    assert reengage_template_for(_surface(template=None, account=True)) is None


async def test_no_template_anywhere_keeps_the_refusal(monkeypatch):
    monkeypatch.setattr(surface_settings, "whatsapp_reengage_template", None)

    assert reengage_template_for(_surface(template=None, account=False)) is None


async def test_a_template_name_meta_would_reject_is_refused_at_the_api():
    with pytest.raises(ValidationError):
        SurfaceWhatsAppConfigInput(reengage_template="Hello There!")


async def test_a_template_is_sent_as_one_meta_accepts():
    sent: list[dict[str, Any]] = []
    client = WhatsAppClient(access_token="t", api_base="https://graph.example.test")

    async def capture(**kwargs: Any) -> str:
        sent.append(kwargs)
        return "wamid.1"

    client.send_message_payload = capture  # type: ignore[method-assign]

    await client.send_template(
        phone_number_id="pn-1",
        to="15550001",
        name="lemma_reengage",
        language="en_US",
        body_parameters=["Ada"],
    )

    [call] = sent
    assert call["payload"]["type"] == "template"
    assert call["payload"]["template"] == {
        "name": "lemma_reengage",
        "language": {"code": "en_US"},
        "components": [
            {"type": "body", "parameters": [{"type": "text", "text": "Ada"}]}
        ],
    }
