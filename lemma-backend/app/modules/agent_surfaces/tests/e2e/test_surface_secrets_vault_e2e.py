"""Surface secrets in the vault, and the rows the previous release wrote.

Between the expand migration (``0078_vault_cutover``) and the next release's
contract, a process still on the previous release can write a secret only to
its old Fernet column. These read such rows, and check that the next write
moves the secret into the vault and clears the old column -- so a previous
release reading the row fails closed instead of using a stale value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

from app.core.crypto import get_secret_cipher
from app.modules.agent_surfaces.domain.web_widgets import WidgetAnswer
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (  # noqa: E501
    SlackConfigurationTokenRepository,
    reveal_tokens,
)
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)
from app.modules.agent_surfaces.platforms.slack.app_configuration import (
    RotatedConfigurationToken,
)

pytestmark = pytest.mark.e2e


async def _pod(client: AsyncClient, org_id: str) -> str:
    created = await client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Vault secrets {uuid4().hex[:8]}",
            "description": "surface secrets e2e",
            "type": "HYBRID",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()["id"]


async def test_a_slack_pair_an_old_release_wrote_is_read_and_moved_on_rotation(
    fixed_test_org, db_session
):
    cipher = get_secret_cipher()
    org_id = UUID(fixed_test_org["id"])
    await db_session.execute(
        text(
            "INSERT INTO slack_configuration_tokens (id, organization_id, team_id, "
            "access_token, refresh_token, access_expires_at, saved_at, created_at, "
            "updated_at) VALUES (gen_random_uuid(), :org, 'TOLD', :access, "
            ":refresh, now(), now(), now(), now())"
        ),
        {
            "org": org_id,
            "access": cipher.encrypt_str("xoxe.xoxp-old-access"),
            "refresh": cipher.encrypt_str("xoxe-old-refresh"),
        },
    )
    await db_session.commit()
    tokens = SlackConfigurationTokenRepository(db_session)

    row = await tokens.get(org_id, "TOLD")
    assert row is not None and row.tokens_secret_id is None
    pair = await reveal_tokens(db_session, row)
    assert pair.access_token.get_secret_value() == "xoxe.xoxp-old-access"
    assert pair.refresh_token.get_secret_value() == "xoxe-old-refresh"

    stored = await tokens.store_rotation(
        row.id,
        generation=row.generation,
        token=RotatedConfigurationToken(
            access_token=SecretStr("xoxe.xoxp-new-access"),
            refresh_token=SecretStr("xoxe-new-refresh"),
            team_id="TOLD",
            slack_user_id=None,
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        ),
    )
    await db_session.commit()
    assert stored

    row = await tokens.get(org_id, "TOLD")
    assert row is not None
    assert row.tokens_secret_id is not None
    assert (row.access_token, row.refresh_token) == (None, None)
    pair = await reveal_tokens(db_session, row)
    assert pair.access_token.get_secret_value() == "xoxe.xoxp-new-access"
    assert pair.refresh_token.get_secret_value() == "xoxe-new-refresh"

    # The next rotation replaces the same secret rather than adding one.
    secret_id = row.tokens_secret_id
    assert await tokens.store_rotation(
        row.id,
        generation=row.generation,
        token=RotatedConfigurationToken(
            access_token=SecretStr("xoxe.xoxp-third-access"),
            refresh_token=SecretStr("xoxe-third-refresh"),
            team_id="TOLD",
            slack_user_id=None,
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        ),
    )
    await db_session.commit()
    row = await tokens.get(org_id, "TOLD")
    assert row is not None and row.tokens_secret_id == secret_id
    pair = await reveal_tokens(db_session, row)
    assert pair.access_token.get_secret_value() == "xoxe.xoxp-third-access"


async def test_a_widget_secret_is_in_the_vault_and_an_old_rows_moves_on_rotation(
    authenticated_client: AsyncClient, fixed_test_org, db_session
):
    pod_id = UUID(await _pod(authenticated_client, fixed_test_org["id"]))
    widgets = WebWidgetRepository(db_session)
    widget = await widgets.create(
        pod_id=pod_id,
        # The pod's own assistant is the agent whose id is the pod's.
        agent_id=pod_id,
        name="site",
        public_key=f"pk_{uuid4().hex}",
        secret="widget-signing-secret",
        allowed_origins=[],
        answer=WidgetAnswer.OFF,
        looked_after_by=None,
        host_issuer="default",
    )
    await db_session.commit()

    secret_id, legacy, purpose = (
        await db_session.execute(
            text(
                "SELECT w.signing_secret_id, w.signing_secret, s.purpose "
                "FROM agent_surface_web_widgets w "
                "JOIN vault_secrets s ON s.id = w.signing_secret_id WHERE w.id = :id"
            ),
            {"id": widget.id},
        )
    ).one()
    assert legacy is None
    assert purpose == "agent_surfaces.web_widget.signing_secret"
    assert await widgets.signing_secret(widget.id) == "widget-signing-secret"

    # A previous release rotated it: the old column only.
    await db_session.execute(
        text(
            "UPDATE agent_surface_web_widgets SET signing_secret_id = NULL, "
            "signing_secret = :legacy WHERE id = :id"
        ),
        {"id": widget.id, "legacy": get_secret_cipher().encrypt_str("rotated-old")},
    )
    await db_session.commit()
    assert await widgets.signing_secret(widget.id) == "rotated-old"

    await widgets.rotate_secret(widget_id=widget.id, secret="rotated-new")
    await db_session.commit()
    moved, legacy = (
        await db_session.execute(
            text(
                "SELECT signing_secret_id, signing_secret "
                "FROM agent_surface_web_widgets WHERE id = :id"
            ),
            {"id": widget.id},
        )
    ).one()
    assert moved is not None and moved != secret_id
    assert legacy is None
    assert await widgets.signing_secret(widget.id) == "rotated-new"
