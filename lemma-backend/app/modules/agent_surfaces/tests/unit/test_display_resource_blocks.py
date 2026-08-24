"""Every surface fences the preview block in its own monospace.

The block is the one part of a card whose meaning is in its layout: it arrives
with columns already aligned to a fixed width, and a platform that renders it as
proportional text turns that alignment into ragged noise. So each renderer has
to wrap it — and the wrapper differs per platform, which is why this is checked
per platform rather than once.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
)
from app.modules.agent_surfaces.platforms.email_render import (
    render_display_resource_email_html,
)
from app.modules.agent_surfaces.platforms.slack.message_blocks import (
    _display_resource_blocks,
)
from app.modules.agent_surfaces.platforms.teams.cards import (
    _teams_display_resource_card,
)
from app.modules.agent_surfaces.platforms.telegram.service import (
    _telegram_display_resource_text,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    whatsapp_display_resource_text,
)

BLOCK = "id  stage\n--  -----\n1   won"


def _plan() -> SurfaceDisplayRenderPlan:
    return SurfaceDisplayRenderPlan(
        resource_type="TABLE",
        title="Table: deals",
        summary="1 of 42 records",
        preview_block=BLOCK,
        actions=[
            SurfaceDisplayAction(
                label="Open in Lemma", url="https://app.example.test/pod/p/data"
            )
        ],
    )


def test_telegram_sends_the_block_as_html_preformatted_text():
    text = _telegram_display_resource_text(_plan())

    assert f"<pre>{BLOCK}</pre>" in text
    assert "1 of 42 records" in text


def test_slack_fences_the_block_inside_the_section():
    blocks = _display_resource_blocks(_plan())

    section = blocks[0]["text"]["text"]
    assert "```\n" + BLOCK + "\n```" in section


def test_whatsapp_fences_the_block_so_its_own_monospace_survives():
    text = whatsapp_display_resource_text(_plan())

    assert "```\n" + BLOCK + "\n```" in text


def test_teams_gives_the_block_a_monospace_text_block():
    card = _teams_display_resource_card(_plan())

    monospace = [item for item in card["body"] if item.get("fontType") == "Monospace"]
    assert len(monospace) == 1
    assert monospace[0]["text"] == BLOCK
    # Wrapping is what would destroy the alignment, so it stays off.
    assert monospace[0]["wrap"] is False


def test_email_puts_the_block_in_a_pre():
    html = render_display_resource_email_html([_plan()])

    assert "<pre" in html
    assert "1   won" in html


def test_a_card_without_a_block_is_unchanged_everywhere():
    plan = _plan().model_copy(update={"preview_block": None})

    assert "<pre>" not in _telegram_display_resource_text(plan)
    assert "```" not in _display_resource_blocks(plan)[0]["text"]["text"]
    assert "```" not in whatsapp_display_resource_text(plan)
    assert not [
        item
        for item in _teams_display_resource_card(plan)["body"]
        if item.get("fontType") == "Monospace"
    ]
    assert "<pre" not in render_display_resource_email_html([plan])
