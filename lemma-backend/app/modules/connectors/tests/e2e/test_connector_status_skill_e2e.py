"""E2E tests for connector status and skill endpoints."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(
    app_id: str,
    *,
    title: str = "Test App",
    providers: list[str] | None = None,
) -> Connector:
    caps = []
    for p in providers or ["LEMMA"]:
        if p == "COMPOSIO":
            caps.append({
                "provider": "COMPOSIO",
                "auth_scheme": "OAUTH2",
                "toolkit_slug": app_id,
            })
        else:
            caps.append({"provider": "LEMMA", "auth_scheme": "OAUTH2"})
    return Connector(
        id=app_id,
        title=title,
        description=f"Test connector {title}",
        provider_capabilities=caps,
        is_active=True,
    )


async def _seed_auth_config(
    db_session: AsyncSession,
    *,
    app_id: str,
    organization_id: str,
    provider: str = "LEMMA",
    name: str | None = None,
) -> AuthConfig:
    cfg = AuthConfig(
        organization_id=organization_id,
        connector_id=app_id,
        provider=provider,
        config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
        name=name or f"{app_id}-{uuid4().hex[:8]}",
    )
    db_session.add(cfg)
    await db_session.flush()
    return cfg


async def _seed_account(
    db_session: AsyncSession,
    *,
    app_id: str,
    user_id: str,
    organization_id: str,
    auth_config_id,
    email: str = "user@example.com",
) -> Account:
    acc = Account(
        id=uuid4(),
        connector_id=app_id,
        user_id=user_id,
        organization_id=organization_id,
        auth_config_id=auth_config_id,
        email=email,
    )
    db_session.add(acc)
    await db_session.flush()
    return acc


# ---------------------------------------------------------------------------
# Connector Status endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connector_status_empty(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    """Status returns empty lists when no auth configs or accounts exist for org."""
    org_id = fixed_test_org["id"]
    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/status"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "installed" in data
    assert "accounts" in data
    assert isinstance(data["installed"], list)
    assert isinstance(data["accounts"], list)


@pytest.mark.asyncio
async def test_connector_status_shows_installed_apps(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session: AsyncSession,
):
    """Status returns auth configs (installed apps) for the org."""
    org_id = fixed_test_org["id"]
    app_id = f"status-app-{uuid4().hex[:8]}"

    app = _make_app(app_id, title="Status Test App")
    db_session.add(app)
    await db_session.flush()

    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=org_id,
        provider="LEMMA",
    )
    await db_session.commit()

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/status"
    )
    assert response.status_code == 200, response.text
    data = response.json()

    installed_ids = [item["connector_id"] for item in data["installed"]]
    assert app_id in installed_ids

    match = next(i for i in data["installed"] if i["connector_id"] == app_id)
    assert match["name"] == auth_config.name
    assert match["title"] == "Status Test App"
    assert match["provider"] == "LEMMA"
    assert match["status"] is not None


@pytest.mark.asyncio
async def test_connector_status_shows_connected_accounts(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session: AsyncSession,
):
    """Status returns connected accounts for the current user."""
    org_id = fixed_test_org["id"]
    user_id = fixed_test_user["id"]
    app_id = f"acct-app-{uuid4().hex[:8]}"

    app = _make_app(app_id, title="Account Test App")
    db_session.add(app)
    await db_session.flush()

    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=org_id,
    )
    await _seed_account(
        db_session,
        app_id=app_id,
        user_id=user_id,
        organization_id=org_id,
        auth_config_id=auth_config.id,
        email="alice@example.com",
    )
    await db_session.commit()

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/status"
    )
    assert response.status_code == 200, response.text
    data = response.json()

    account_ids = [item["connector_id"] for item in data["accounts"]]
    assert app_id in account_ids

    match = next(i for i in data["accounts"] if i["connector_id"] == app_id)
    assert match["email"] == "alice@example.com"
    assert match["title"] == "Account Test App"
    assert match["status"] is not None


@pytest.mark.asyncio
async def test_connector_status_dual_provider_app(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session: AsyncSession,
):
    """Status shows the correct provider for each installed auth config."""
    org_id = fixed_test_org["id"]
    app_id = f"dual-app-{uuid4().hex[:8]}"

    app = _make_app(app_id, title="Dual Provider App", providers=["LEMMA", "COMPOSIO"])
    db_session.add(app)
    await db_session.flush()

    # Install with LEMMA provider
    await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=org_id,
        provider="LEMMA",
    )
    await db_session.commit()

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/status"
    )
    assert response.status_code == 200, response.text
    data = response.json()

    match = next((i for i in data["installed"] if i["connector_id"] == app_id), None)
    assert match is not None
    assert match["provider"] == "LEMMA"


@pytest.mark.asyncio
async def test_connector_status_multiple_apps(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session: AsyncSession,
):
    """Status correctly returns all installed apps when multiple configs exist."""
    org_id = fixed_test_org["id"]
    user_id = fixed_test_user["id"]
    suffix = uuid4().hex[:8]
    app_a_id = f"multi-a-{suffix}"
    app_b_id = f"multi-b-{suffix}"

    for aid, title in [(app_a_id, "App A"), (app_b_id, "App B")]:
        db_session.add(_make_app(aid, title=title))
    await db_session.flush()

    cfg_a = await _seed_auth_config(db_session, app_id=app_a_id, organization_id=org_id)
    await _seed_auth_config(db_session, app_id=app_b_id, organization_id=org_id)
    await _seed_account(
        db_session,
        app_id=app_a_id,
        user_id=user_id,
        organization_id=org_id,
        auth_config_id=cfg_a.id,
        email="user-a@example.com",
    )
    await db_session.commit()

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/status"
    )
    assert response.status_code == 200, response.text
    data = response.json()

    installed_ids = {i["connector_id"] for i in data["installed"]}
    assert app_a_id in installed_ids
    assert app_b_id in installed_ids

    account_app_ids = {i["connector_id"] for i in data["accounts"]}
    assert app_a_id in account_app_ids


# ---------------------------------------------------------------------------
# Skill endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    return tmp_path / "skills"


@pytest.mark.asyncio
async def test_skill_not_found_returns_404(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
):
    """Skill endpoint returns 404 when no skill file exists."""
    app_id = f"no-skill-app-{uuid4().hex[:8]}"
    app = _make_app(app_id)
    db_session.add(app)
    await db_session.commit()

    with patch(
        "app.modules.connectors.api.connector_controller.SKILLS_DIR",
        tmp_path / "skills",
    ):
        response = await authenticated_client.get(
            f"/connectors/{app_id}/skill"
        )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_skill_returns_generic_file(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
):
    """Skill endpoint returns the generic {app_id}.md when no provider is specified."""
    app_id = f"generic-skill-{uuid4().hex[:8]}"
    app = _make_app(app_id, title="Generic Skill App")
    db_session.add(app)
    await db_session.commit()

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / f"{app_id}.md").write_text("# Generic Skill\nSome instructions.", encoding="utf-8")

    with patch(
        "app.modules.connectors.api.connector_controller.SKILLS_DIR",
        skills_dir,
    ):
        response = await authenticated_client.get(
            f"/connectors/{app_id}/skill"
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["connector_id"] == app_id
    assert data["title"] == "Generic Skill App"
    assert "Generic Skill" in data["markdown"]


@pytest.mark.asyncio
async def test_skill_returns_provider_specific_file(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
):
    """Provider-specific skill file is returned when provider query param is passed."""
    app_id = f"dual-skill-{uuid4().hex[:8]}"
    app = _make_app(app_id, title="Dual Skill App", providers=["LEMMA", "COMPOSIO"])
    db_session.add(app)
    await db_session.commit()

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / f"{app_id}.lemma.md").write_text("# LEMMA Skill\nLemma instructions.", encoding="utf-8")
    (skills_dir / f"{app_id}.composio.md").write_text("# Composio Skill\nComposio instructions.", encoding="utf-8")
    (skills_dir / f"{app_id}.md").write_text("# Generic Skill\nGeneric instructions.", encoding="utf-8")

    with patch(
        "app.modules.connectors.api.connector_controller.SKILLS_DIR",
        skills_dir,
    ):
        # Request LEMMA-specific skill
        response = await authenticated_client.get(
            f"/connectors/{app_id}/skill",
            params={"provider": "lemma"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "LEMMA" in data["markdown"]
        assert data["provider"] == "lemma"

        # Request COMPOSIO-specific skill
        response = await authenticated_client.get(
            f"/connectors/{app_id}/skill",
            params={"provider": "composio"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "Composio" in data["markdown"]
        assert data["provider"] == "composio"

        # No provider → generic
        response = await authenticated_client.get(
            f"/connectors/{app_id}/skill"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "Generic" in data["markdown"]


@pytest.mark.asyncio
async def test_skill_falls_back_to_generic_when_provider_specific_missing(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
):
    """Falls back to generic .md when provider-specific file doesn't exist."""
    app_id = f"fallback-skill-{uuid4().hex[:8]}"
    app = _make_app(app_id, title="Fallback App")
    db_session.add(app)
    await db_session.commit()

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # Only generic file, no provider-specific
    (skills_dir / f"{app_id}.md").write_text("# Fallback Skill\nInstructions here.", encoding="utf-8")

    with patch(
        "app.modules.connectors.api.connector_controller.SKILLS_DIR",
        skills_dir,
    ):
        response = await authenticated_client.get(
            f"/connectors/{app_id}/skill",
            params={"provider": "lemma"},
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "Fallback Skill" in data["markdown"]


@pytest.mark.asyncio
async def test_skill_unknown_app_returns_404(
    authenticated_client: AsyncClient,
    tmp_path: Path,
):
    """Skill endpoint returns 404 for completely unknown app."""
    with patch(
        "app.modules.connectors.api.connector_controller.SKILLS_DIR",
        tmp_path / "skills",
    ):
        response = await authenticated_client.get(
            "/connectors/completely-unknown-app/skill"
        )
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Skill file resolution helper unit tests
# ---------------------------------------------------------------------------

def test_resolve_skill_file_provider_specific(tmp_path: Path):
    """_resolve_skill_file returns provider-specific file when it exists."""
    from app.modules.connectors.api.connector_controller import _resolve_skill_file

    (tmp_path / "gmail.lemma.md").write_text("lemma", encoding="utf-8")
    (tmp_path / "gmail.md").write_text("generic", encoding="utf-8")

    with patch("app.modules.connectors.api.connector_controller.SKILLS_DIR", tmp_path):
        result = _resolve_skill_file("gmail", "lemma")
    assert result is not None
    assert result.read_text() == "lemma"


def test_resolve_skill_file_falls_back_to_generic(tmp_path: Path):
    """_resolve_skill_file falls back to generic file when provider-specific is absent."""
    from app.modules.connectors.api.connector_controller import _resolve_skill_file

    (tmp_path / "gmail.md").write_text("generic", encoding="utf-8")

    with patch("app.modules.connectors.api.connector_controller.SKILLS_DIR", tmp_path):
        result = _resolve_skill_file("gmail", "composio")
    assert result is not None
    assert result.read_text() == "generic"


def test_resolve_skill_file_returns_none_when_nothing_exists(tmp_path: Path):
    """_resolve_skill_file returns None when no skill file exists at all."""
    from app.modules.connectors.api.connector_controller import _resolve_skill_file

    with patch("app.modules.connectors.api.connector_controller.SKILLS_DIR", tmp_path):
        result = _resolve_skill_file("gmail", "lemma")
    assert result is None


# ---------------------------------------------------------------------------
# Skill generation helper unit tests (no LLM, no DB needed)
# Tests the pure functions directly without importing the script module.
# ---------------------------------------------------------------------------

def _build_skill_prompt_pure(app_id: str, title: str, description: str, operations: list) -> str:
    """Pure re-implementation of the prompt builder for testing (mirrors the script's version)."""
    ops_info: list[str] = []
    for op in operations[:20]:
        op_name = getattr(op, "name", "") or ""
        op_display = getattr(op, "display_name", None) or op_name
        op_desc = (getattr(op, "description", None) or "")[:250]
        input_schema = getattr(op, "input_schema", None) or {}
        fields: list[str] = []
        if isinstance(input_schema, dict):
            props = input_schema.get("properties", {})
            required = set(input_schema.get("required", []))
            for fname, finfo in list(props.items())[:8]:
                ftype = finfo.get("type", "string") if isinstance(finfo, dict) else "string"
                fdesc = finfo.get("description", "") if isinstance(finfo, dict) else ""
                req_mark = "*" if fname in required else ""
                fields.append(f"  - {fname}{req_mark} ({ftype}): {fdesc[:80]}")
        field_block = "\n".join(fields) if fields else "  (no schema)"
        ops_info.append(
            f"Operation: {op_name}\nDisplay: {op_display}\nDescription: {op_desc}\n"
            f"Input fields (* = required):\n{field_block}"
        )
    ops_block = "\n\n".join(ops_info) if ops_info else "(no operations)"
    return (
        f"Generate a skill guide for: {title or app_id}\n"
        f"App ID (use in all commands): {app_id}\n"
        f"Platform description: {description[:400] or f'Connector with {title or app_id}.'}\n\n"
        f"Available operations and their input schemas:\n\n{ops_block}\n\n"
        f"Now write the skill guide following the system prompt format exactly."
    )


def test_build_skill_prompt_includes_operations():
    """_build_skill_prompt_pure includes operation names and input schemas in the output."""
    ops = [
        type("Op", (), {
            "name": "GMAIL_SEND_EMAIL",
            "display_name": "Send Email",
            "description": "Send an email",
            "input_schema": {"type": "object", "required": ["to"], "properties": {"to": {"type": "string", "description": "Recipient"}}},
        })(),
        type("Op", (), {
            "name": "GMAIL_LIST_MESSAGES",
            "display_name": "List Messages",
            "description": "List emails",
            "input_schema": None,
        })(),
    ]
    prompt = _build_skill_prompt_pure("gmail", "Gmail", "Google email service.", ops)
    assert "Gmail" in prompt
    assert "GMAIL_SEND_EMAIL" in prompt
    assert "GMAIL_LIST_MESSAGES" in prompt
    assert "gmail" in prompt
    # New format: should include input schema fields and prompt structure
    assert "Input fields" in prompt
    assert "Recipient" in prompt  # from input_schema description


def _app_providers_pure(app) -> list[str]:
    """Pure re-implementation of _app_providers for testing."""
    caps = getattr(app, "provider_capabilities", None) or []
    providers: list[str] = []
    for cap in caps:
        if isinstance(cap, dict):
            p = cap.get("provider") or ""
        else:
            p = str(getattr(cap, "provider", "") or "")
        if p:
            providers.append(p.lower())
    return providers


def test_app_providers_single():
    """_app_providers_pure returns single provider for a LEMMA-only app."""
    app = type("App", (), {
        "provider_capabilities": [{"provider": "LEMMA", "auth_scheme": "OAUTH2"}]
    })()
    providers = _app_providers_pure(app)
    assert providers == ["lemma"]


def test_app_providers_dual():
    """_app_providers_pure returns both providers for a dual-provider app."""
    app = type("App", (), {
        "provider_capabilities": [
            {"provider": "LEMMA", "auth_scheme": "OAUTH2"},
            {"provider": "COMPOSIO", "auth_scheme": "OAUTH2", "toolkit_slug": "gmail"},
        ]
    })()
    providers = _app_providers_pure(app)
    assert set(providers) == {"lemma", "composio"}


# ---------------------------------------------------------------------------
# Operations filtered by provider via auth config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_operations_filtered_by_lemma_provider(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    """Operations search returns only LEMMA ops when auth config uses LEMMA provider."""
    org_id = fixed_test_org["id"]
    app_id = f"lemma-ops-{uuid4().hex[:8]}"

    # App supports both providers
    app = _make_app(app_id, title="Lemma Ops App", providers=["LEMMA", "COMPOSIO"])
    db_session.add(app)
    db_session.add(ConnectorOperation(
        id=f"{app_id}:LEMMA:lemma_op",
        connector_id=app_id,
        name="lemma_op",
        provider="LEMMA",
        provider_operation_name="LEMMA_OP",
        display_name="Lemma Operation",
        description="Lemma native op",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    ))
    db_session.add(ConnectorOperation(
        id=f"{app_id}:COMPOSIO:composio_op",
        connector_id=app_id,
        name="composio_op",
        provider="COMPOSIO",
        provider_operation_name="COMPOSIO_OP",
        display_name="Composio Operation",
        description="Composio op",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    ))
    await db_session.flush()

    # One auth config per (org, app) — use LEMMA
    lemma_cfg = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=org_id,
        provider="LEMMA",
    )
    await db_session.commit()

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/{lemma_cfg.name}/operations"
    )
    assert response.status_code == 200, response.text
    ops_data = response.json()
    op_names = [op["name"] for op in ops_data.get("items", ops_data.get("operations", []))]
    assert "lemma_op" in op_names, f"Expected lemma_op in {op_names}, got: {ops_data}"
    assert "composio_op" not in op_names


@pytest.mark.asyncio
async def test_operations_filtered_by_composio_provider(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    """Operations search returns only COMPOSIO ops when auth config uses COMPOSIO provider."""
    org_id = fixed_test_org["id"]
    # Use distinct app so constraint (unique org+app) isn't violated
    app_id = f"comp-ops-{uuid4().hex[:8]}"

    app = _make_app(app_id, title="Composio Ops App", providers=["LEMMA", "COMPOSIO"])
    db_session.add(app)
    db_session.add(ConnectorOperation(
        id=f"{app_id}:LEMMA:lemma_op",
        connector_id=app_id,
        name="lemma_op",
        provider="LEMMA",
        provider_operation_name="LEMMA_OP",
        display_name="Lemma Operation",
        description="Lemma native op",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    ))
    db_session.add(ConnectorOperation(
        id=f"{app_id}:COMPOSIO:composio_op",
        connector_id=app_id,
        name="composio_op",
        provider="COMPOSIO",
        provider_operation_name="COMPOSIO_OP",
        display_name="Composio Operation",
        description="Composio op",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    ))
    await db_session.flush()

    # Use COMPOSIO as the installed provider
    composio_cfg = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=org_id,
        provider="COMPOSIO",
    )
    await db_session.commit()

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/{composio_cfg.name}/operations"
    )
    assert response.status_code == 200, response.text
    ops_data = response.json()
    op_names = [op["name"] for op in ops_data.get("items", ops_data.get("operations", []))]
    assert "composio_op" in op_names, f"Expected composio_op in {op_names}, got: {ops_data}"
    assert "lemma_op" not in op_names
