"""Import the connector app catalog into the database.

This script syncs connector apps, their operations, and triggers into the
connector catalog. It supports two providers:

1. **Lemma native apps** — always imported. These are defined in
   ``scripts/lemma_apps_config.json`` (Slack, Jira, Confluence, etc.) and
   in the ``lemma-connectors`` package (Gmail, Google Calendar, etc.).

2. **Composio apps** — imported only when ``COMPOSIO_API_KEY`` is set.
   Without a key, the Composio portion is skipped gracefully and only native
   apps are synced.

Usage::

    # Import everything (native + Composio if key is set, native-only otherwise)
    python scripts/import_connector_catalog.py

    # Native apps only
    python scripts/import_connector_catalog.py --provider native

    # Composio apps only (requires COMPOSIO_API_KEY)
    python scripts/import_connector_catalog.py --provider composio

    # Import a single app
    python scripts/import_connector_catalog.py --app gmail --app slack

    # Dry run — fetch and log without committing
    python scripts/import_connector_catalog.py --dry-run
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import ast
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent.parent))


def _load_repo_env() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env", override=False)


_load_repo_env()

from app.core.config import settings
from app.modules.connectors.config import connector_settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.infrastructure.adapters.lemma_connector_factory import (
    create_lemma_info_client,
    describe_lemma_connector,
    supported_lemma_connectors,
)
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    AuthMethod,
    AuthProvider,
    ComposioProviderCapability,
    LemmaProviderCapability,
    OAuth2Defaults,
    SystemOAuthCredentialRef,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.connectors.infrastructure.adapters.schema_compiler import (
    PydanticCodeSchemaCompiler,
)
from app.modules.connectors.infrastructure.repositories.connector_operation_repository import (
    ConnectorOperationRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_repository import (
    ConnectorRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_trigger_repository import (
    ConnectorTriggerRepository,
)
from app.core.log.log import get_logger, setup_logging

os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

try:
    from composio import Composio
except Exception as exc:  # pragma: no cover - import path depends on local env
    raise SystemExit(
        "Failed to import composio. Run this script with the repo virtualenv "
        "after dependencies are installed."
    ) from exc

setup_logging()
logger = get_logger(__name__)

COMPOSIO_TOOLKIT_TO_CONNECTOR_ID = {
    "gmail": "gmail",
    "googlecalendar": "google_calendar",
    "googledrive": "google_drive",
    "googledocs": "google_docs",
    "googlesheets": "google_sheets",
}
COMPOSIO_CONNECTOR_ID_TO_TOOLKIT = {
    connector_id: toolkit_slug
    for toolkit_slug, connector_id in COMPOSIO_TOOLKIT_TO_CONNECTOR_ID.items()
}
COMPOSIO_NATIVE_CONNECTOR_IDS = set(COMPOSIO_TOOLKIT_TO_CONNECTOR_ID.values())
NATIVE_OPERATION_CONNECTOR_IDS = (
    supported_lemma_connectors()
)
NATIVE_AUTH_METHOD_OVERRIDES: dict[str, AuthMethod] = {
    "apollo": AuthMethod.API_KEY,
    "airtable": AuthMethod.API_KEY,
    "clickup": AuthMethod.API_KEY,
}
LEMMA_AUTH_PROVIDER_CONNECTOR_IDS = NATIVE_OPERATION_CONNECTOR_IDS | {"confluence"}
COMPOSIO_EXCLUDED_CONNECTOR_IDS = {
    "microsoft_teams",
}
DEFAULT_COMPOSIO_CONNECTOR_IDS: tuple[str, ...] = (
    "gmail",
    "googlecalendar",
    "googledrive",
    "googledocs",
    "googlesheets",
    "airtable",
    "asana",
    "box",
    "cal",
    "calendly",
    "canva",
    "clickup",
    "discord",
    "dropbox",
    "excel",
    "facebook",
    "figma",
    "freshdesk",
    "google_analytics",
    "google_chat",
    "googleads",
    "googlecontacts",
    "googleforms",
    "googlemeet",
    "googleslides",
    "googletasks",
    "hubspot",
    "instagram",
    "intercom",
    "linear",
    "linkedin",
    "linkedin_ads",
    "mailchimp",
    "metaads",
    "metabase",
    "miro",
    "mixpanel",
    "monday",
    "notion",
    "one_drive",
    "openweather_api",
    "outlook",
    "paypal",
    "posthog",
    "quickbooks",
    "razorpay",
    "reddit",
    "reddit_ads",
    "resend",
    "salesforce",
    "segment",
    "semrush",
    "sentry",
    "servicenow",
    "splitwise",
    "spotify",
    "square",
    "stripe",
    "tiktok",
    "todoist",
    "trello",
    "twitter",
    "youtube",
    "zendesk",
    "zoho_books",
    "zoho_inventory",
    "zoho_invoice",
    "zoho_mail",
    "zoom",
)
COMPOSIO_EXTRA_CONNECTOR_IDS_ENV = "COMPOSIO_EXTRA_APP_IDS"
IMPORT_BATCH_OPERATION_CHUNK_SIZE = 100

# Lemma native apps config loaded from JSON (committed to repo, no secrets)
LEMMA_APPS_CONFIG_PATH = Path(__file__).parent / "lemma_apps_config.json"

def _load_lemma_apps_config() -> list[dict]:
    """Load Lemma native apps configuration from JSON file."""
    if not LEMMA_APPS_CONFIG_PATH.exists():
        logger.warning("Lemma apps config not found at %s", LEMMA_APPS_CONFIG_PATH)
        return []
    with open(LEMMA_APPS_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _build_operation_search_document(
    *,
    public_name: str,
    display_name: str | None,
    description: str | None,
) -> str:
    chunks: list[str] = [
        public_name,
        public_name.replace("_", " "),
    ]
    chunks.extend(filter(None, [display_name, description]))

    seen: set[str] = set()
    normalized_chunks: list[str] = []
    for chunk in chunks:
        normalized = " ".join(str(chunk).replace("_", " ").split()).strip()
        lowered = normalized.lower()
        if not lowered or lowered in seen:
            continue
        normalized_chunks.append(normalized)
        seen.add(lowered)
    return "\n".join(normalized_chunks)

get_native_info_client = create_lemma_info_client

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import the connector app catalog into the database. "
            "Native (Lemma) apps are always synced. "
            "Composio apps are synced only when COMPOSIO_API_KEY is set."
        ),
    )
    parser.add_argument(
        "--provider",
        default="all",
        choices=["all", "composio", "native"],
        help="Which provider catalog to sync.",
    )
    parser.add_argument(
        "--app",
        action="append",
        dest="apps",
        help="Sync only the specified app/toolkit slug. Can be repeated.",
    )
    parser.add_argument(
        "--managed-by",
        default="composio",
        choices=["composio", "all", "project"],
        help="Which Composio toolkit catalog to import from.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=200,
        help="Page size for Composio catalog requests.",
    )
    parser.add_argument(
        "--max-composio-apps",
        type=int,
        default=10,
        help="Legacy safety limit for broad Composio imports. Curated allowlist imports ignore this.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and log catalog entries without committing database changes.",
    )
    parser.add_argument(
        "--generate-skills",
        action="store_true",
        help="After syncing, generate skill markdown docs for each app using LLM (saved to app/modules/connectors/skills/).",
    )
    return parser.parse_args()


def _parse_slug_list(value: str | None) -> set[str]:
    if not value:
        return set()
    slugs: set[str] = set()
    for raw_item in value.split(","):
        slug = raw_item.strip()
        if not slug:
            continue
        slugs.add(slug)
    return slugs


def _filter_composio_connector_ids(app_slugs: set[str]) -> set[str]:
    return {
        slug.strip()
        for slug in app_slugs
        if slug.strip()
        and _normalize_connector_id(slug) != "composio"
        and _normalize_connector_id(slug) not in COMPOSIO_EXCLUDED_CONNECTOR_IDS
    }


def _default_composio_connector_ids() -> set[str]:
    selected = {slug.strip() for slug in DEFAULT_COMPOSIO_CONNECTOR_IDS}
    selected.update(_parse_slug_list(os.getenv(COMPOSIO_EXTRA_CONNECTOR_IDS_ENV)))
    return _filter_composio_connector_ids(selected)


def _infer_composio_auth_method(toolkit_item, toolkit_detail) -> AuthMethod:
    if getattr(toolkit_item, "no_auth", False):
        return AuthMethod.NOAUTH

    schemes = set()
    for scheme in getattr(toolkit_item, "auth_schemes", None) or []:
        schemes.add(str(scheme).upper())
    for scheme in getattr(toolkit_item, "composio_managed_auth_schemes", None) or []:
        schemes.add(str(scheme).upper())
    for detail in getattr(toolkit_detail, "auth_config_details", None) or []:
        schemes.add(str(detail.mode).upper())

    if "NO_AUTH" in schemes:
        return AuthMethod.NOAUTH
    if schemes & {"OAUTH1", "OAUTH2", "COMPOSIO_LINK"}:
        return AuthMethod.OAUTH2
    return AuthMethod.API_KEY


def _infer_native_auth_method(
    app_slug: str,
    existing: ConnectorEntity | None,
) -> AuthMethod:
    if existing:
        try:
            return existing.capability_for(AuthProvider.LEMMA).auth_scheme
        except ValueError:
            pass
    return NATIVE_AUTH_METHOD_OVERRIDES.get(app_slug, AuthMethod.OAUTH2)


def _env_names(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _env_available(value: object) -> bool:
    return any(bool(os.getenv(name)) for name in _env_names(value))


def _system_oauth_available(system_oauth: dict[str, object] | None) -> bool:
    if not system_oauth:
        return False
    return _env_available(system_oauth.get("client_id_env")) and _env_available(
        system_oauth.get("client_secret_env")
    )


def _existing_capabilities(
    existing: ConnectorEntity | None,
) -> dict[AuthProvider, object]:
    if not existing:
        return {}
    return {AuthProvider(capability.provider.value): capability for capability in existing.provider_capabilities}


def _merge_provider_capabilities(
    existing: ConnectorEntity | None,
    *capabilities: object | None,
) -> list[object]:
    merged = _existing_capabilities(existing)
    for capability in capabilities:
        if capability is None:
            continue
        merged[AuthProvider(capability.provider.value)] = capability
    return [
        merged[provider]
        for provider in (AuthProvider.LEMMA, AuthProvider.COMPOSIO)
        if provider in merged
    ]


def _native_package_provider_capability(
    connector_id: str,
    existing: ConnectorEntity | None,
) -> LemmaProviderCapability:
    auth_method = _infer_native_auth_method(connector_id, existing)
    if existing:
        try:
            capability = existing.capability_for(AuthProvider.LEMMA)
            if isinstance(capability, LemmaProviderCapability):
                updates: dict[str, object] = {"auth_scheme": auth_method}
                if capability.auth_config_schema is None:
                    updates["auth_config_schema"] = _default_auth_config_schema(
                        auth_method
                    )
                return capability.model_copy(update=updates)
        except ValueError:
            pass

    return _lemma_provider_capability(auth_method=auth_method)


def _default_auth_config_schema(auth_method: AuthMethod) -> dict:
    if auth_method != AuthMethod.OAUTH2:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "type": "object",
        "required": ["client_id", "client_secret"],
        "properties": {
            "client_id": {
                "type": "string",
                "title": "Client ID",
            },
            "client_secret": {
                "type": "string",
                "title": "Client secret",
                "format": "password",
            },
        },
        "additionalProperties": False,
    }


def _lemma_provider_capability(
    *,
    auth_method: AuthMethod,
    oauth2_defaults: dict | None = None,
    auth_config_schema: dict | None = None,
    credential_schema: dict | None = None,
    system_oauth: dict | None = None,
) -> LemmaProviderCapability:
    system_default_available = (
        auth_method != AuthMethod.OAUTH2 or _system_oauth_available(system_oauth)
    )
    return LemmaProviderCapability(
        auth_scheme=auth_method,
        oauth2_defaults=OAuth2Defaults.model_validate(oauth2_defaults)
        if oauth2_defaults
        else None,
        auth_config_schema=auth_config_schema
        if auth_config_schema is not None
        else _default_auth_config_schema(auth_method),
        credential_schema=credential_schema,
        system_oauth=SystemOAuthCredentialRef.model_validate(system_oauth)
        if system_oauth
        else None,
        supports_org_custom_oauth=auth_method == AuthMethod.OAUTH2,
        system_default_available=system_default_available,
    )


def _composio_provider_capability(
    *,
    auth_method: AuthMethod,
    toolkit_slug: str,
    auth_config_schema: dict | None = None,
) -> ComposioProviderCapability:
    return ComposioProviderCapability(
        auth_scheme=auth_method,
        toolkit_slug=toolkit_slug,
        auth_config_schema=auth_config_schema,
        system_default_available=True,
        supports_org_custom_auth_config=False,
    )


def _operation_id(connector_id: str, provider: AuthProvider, operation_name: str) -> str:
    return f"{connector_id}:{provider.value.lower()}:{operation_name}"


def _trigger_id(
    connector_id: str, provider: AuthProvider, trigger_slug: str
) -> str:
    return f"{connector_id}:{provider.value.lower()}:{trigger_slug.lower()}"


def _normalize_connector_id(app_slug: str) -> str:
    return app_slug.strip().lower()


def _resolve_composio_toolkit_slug(connector_id: str) -> str:
    return COMPOSIO_CONNECTOR_ID_TO_TOOLKIT.get(
        _normalize_connector_id(connector_id),
        connector_id.strip(),
    )


def _normalize_operation_name(operation_name: str) -> str:
    return operation_name.strip().lower()


def _resolve_composio_connector_id(toolkit_slug: str) -> str:
    raw_slug = toolkit_slug.strip()
    return COMPOSIO_TOOLKIT_TO_CONNECTOR_ID.get(
        _normalize_connector_id(raw_slug),
        raw_slug,
    )


def _uses_native_operations(connector_id: str) -> bool:
    return _normalize_connector_id(connector_id) in NATIVE_OPERATION_CONNECTOR_IDS


def _resolve_composio_provider_operation_name(tool) -> str:
    slug = getattr(tool, "slug", None)
    if not slug:
        raise ValueError("Composio tool is missing a slug")
    return str(slug).strip()


def _resolve_composio_toolkit_versions(
    composio: Composio,
) -> str | Mapping[str, str] | None:
    tools = getattr(composio, "tools", None)
    return getattr(tools, "_toolkit_versions", None)


def _with_composio_toolkit_versions(
    composio: Composio,
    params: dict[str, object],
) -> dict[str, object]:
    toolkit_versions = _resolve_composio_toolkit_versions(composio)
    if toolkit_versions is not None:
        params["toolkit_versions"] = toolkit_versions
    return params


def _humanize_operation_name(operation_name: str) -> str:
    return operation_name.replace("_", " ").strip().capitalize()


def _clean_operation_description(description: str) -> str:
    compact = description.strip()
    for marker in ("\n\nArgs:", "\n\nReturns:", "\n\nRaises:"):
        if marker in compact:
            compact = compact.split(marker, 1)[0]
    compact = compact.split("\n\n", 1)[0]
    return " ".join(compact.split())


def _extract_operation_docstring(
    implementation_content: str | None,
    operation_name: str,
) -> str | None:
    if not implementation_content:
        return None

    try:
        module = ast.parse(implementation_content)
        for node in module.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name == operation_name:
                    return ast.get_docstring(node)

        for node in module.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                docstring = ast.get_docstring(node)
                if docstring:
                    return docstring

        return ast.get_docstring(module)
    except Exception:
        return None


def _resolve_operation_description(
    operation_name: str,
    *,
    description: str | None = None,
    implementation_content: str | None = None,
) -> str:
    if description:
        return _clean_operation_description(description)

    docstring = _extract_operation_docstring(implementation_content, operation_name)
    if docstring:
        return _clean_operation_description(docstring)

    return _humanize_operation_name(operation_name)


def _toolkit_meta_value(toolkit_item, field_name: str) -> str | None:
    meta = getattr(toolkit_item, "meta", None)
    if meta is None:
        return None
    value = getattr(meta, field_name, None)
    return str(value) if value is not None else None


def _is_toolkit_active(toolkit_item) -> bool:
    status = getattr(toolkit_item, "status", None)
    if status is not None:
        return str(status).upper() == "ACTIVE"

    enabled = getattr(toolkit_item, "enabled", None)
    if enabled is not None:
        return bool(enabled)

    return True


def _is_excluded_composio_connector(entity: ConnectorEntity) -> bool:
    normalized_ids = {
        _normalize_connector_id(entity.id),
    }
    for capability in entity.provider_capabilities:
        toolkit_slug = getattr(capability, "toolkit_slug", None)
        if toolkit_slug:
            normalized_ids.add(_normalize_connector_id(toolkit_slug))

    return bool(normalized_ids & COMPOSIO_EXCLUDED_CONNECTOR_IDS) and (
        AuthProvider.COMPOSIO
        in {AuthProvider(capability.provider.value) for capability in entity.provider_capabilities}
    )


async def _deactivate_excluded_composio_connectors(
    connector_repository: ConnectorRepository,
) -> int:
    deactivated_count = 0
    for connector_id in COMPOSIO_EXCLUDED_CONNECTOR_IDS:
        existing = await connector_repository.get(connector_id)
        if (
            not existing
            or not existing.is_active
            or not _is_excluded_composio_connector(existing)
        ):
            continue

        await connector_repository.update(
            existing.model_copy(update={"is_active": False})
        )
        deactivated_count += 1
        logger.info("Deactivated excluded Composio app: %s", existing.id)

    return deactivated_count


async def _deactivate_excluded_composio_connectors_batch(
    connector_repository: ConnectorRepository,
    _operation_repository: ConnectorOperationRepository,
    _trigger_repository: ConnectorTriggerRepository,
) -> tuple[int, int, int]:
    await _deactivate_excluded_composio_connectors(connector_repository)
    return 0, 0, 0


async def _upsert_connector(
    connector_repository: ConnectorRepository,
    entity: ConnectorEntity,
) -> ConnectorEntity:
    existing = await connector_repository.get(entity.id)
    if existing:
        logger.info("Updating connector %s", entity.id)
        return await connector_repository.update(entity)

    logger.info("Creating connector %s", entity.id)
    return await connector_repository.create(entity)


async def _upsert_operation(
    operation_repository: ConnectorOperationRepository,
    connector_id: str,
    *,
    provider: AuthProvider,
    public_name: str,
    provider_operation_name: str,
    display_name: str | None,
    description: str | None,
    input_schema: dict | None,
    output_schema: dict | None,
    search_document: str | None,
    normalize_name: bool = True,
) -> None:
    operation_name = (
        _normalize_operation_name(public_name) if normalize_name else public_name.strip()
    )
    existing = await operation_repository.get_by_connector_provider_and_name(
        connector_id,
        provider.value,
        operation_name,
    )
    entity = ConnectorOperationEntity(
        id=existing.id if existing else _operation_id(connector_id, provider, operation_name),
        connector_id=connector_id,
        provider=provider,
        name=operation_name,
        provider_operation_name=provider_operation_name,
        display_name=display_name,
        description=description,
        search_document=search_document,
        input_schema=input_schema,
        output_schema=output_schema,
    )
    if existing:
        await operation_repository.update(entity)
    else:
        await operation_repository.create(entity)


async def _upsert_trigger(
    trigger_repository: ConnectorTriggerRepository,
    connector_id: str,
    trigger,
    *,
    provider: AuthProvider,
) -> None:
    existing = await trigger_repository.get_by_connector_provider_and_name(
        connector_id,
        provider.value,
        trigger.slug,
    )
    entity = ConnectorTriggerEntity(
        id=existing.id if existing else _trigger_id(connector_id, provider, trigger.slug),
        connector_id=connector_id,
        provider=provider,
        event_type=trigger.slug,
        description=trigger.description,
        config_schema=trigger.config,
        payload_schema=trigger.payload,
        payload_example=None,
    )
    if existing:
        await trigger_repository.update(entity)
    else:
        await trigger_repository.create(entity)


def _paginate_toolkits(composio: Composio, *, managed_by: str, page_size: int):
    cursor = None
    while True:
        response = composio.client.toolkits.list(
            managed_by=managed_by,
            limit=page_size,
            cursor=cursor,
        )
        for item in response.items:
            yield item
        if not response.next_cursor:
            break
        cursor = response.next_cursor


def _paginate_tools(composio: Composio, *, toolkit_slug: str, page_size: int):
    cursor = None
    while True:
        response = composio.client.tools.list(
            **_with_composio_toolkit_versions(
                composio,
                {
                    "toolkit_slug": toolkit_slug,
                    "limit": page_size,
                    "cursor": cursor,
                },
            )
        )
        for item in response.items:
            yield item
        if not response.next_cursor:
            break
        cursor = response.next_cursor


def _paginate_triggers(composio: Composio, *, toolkit_slug: str, page_size: int):
    cursor = None
    while True:
        response = composio.client.triggers_types.list(
            **_with_composio_toolkit_versions(
                composio,
                {
                    "toolkit_slugs": [toolkit_slug],
                    "limit": page_size,
                    "cursor": cursor,
                },
            )
        )
        for item in response.items:
            yield item
        if not response.next_cursor:
            break
        cursor = response.next_cursor


def _list_native_apps(app_filters: set[str] | None) -> list[str]:
    app_slugs = sorted(NATIVE_OPERATION_CONNECTOR_IDS)
    if app_filters:
        normalized_filters = {_normalize_connector_id(slug) for slug in app_filters}
        app_slugs = [slug for slug in app_slugs if slug in normalized_filters]
    return app_slugs


def _list_native_sync_targets(app_filters: set[str] | None) -> list[str]:
    configured_app_slugs = {
        _normalize_connector_id(app_config["name"])
        for app_config in _load_lemma_apps_config()
        if app_config.get("name")
    }
    available_app_slugs = configured_app_slugs | set(_list_native_apps(None))
    if app_filters:
        normalized_filters = {_normalize_connector_id(slug) for slug in app_filters}
        available_app_slugs &= normalized_filters
    return sorted(available_app_slugs)


def _list_composio_toolkits(
    composio: Composio,
    *,
    app_filters: set[str] | None,
    managed_by: str,
    page_size: int,
    max_composio_apps: int,
):
    if app_filters:
        selected_app_slugs = {slug.strip() for slug in app_filters}
    else:
        selected_app_slugs = _default_composio_connector_ids()
    selected_app_slugs = _filter_composio_connector_ids(selected_app_slugs)
    toolkit_slugs = sorted(
        {_resolve_composio_toolkit_slug(app_slug) for app_slug in selected_app_slugs}
    )
    items = []
    for toolkit_slug in toolkit_slugs:
        try:
            items.append(composio.toolkits.get(toolkit_slug))
        except Exception as exc:
            logger.warning("Skipping unknown Composio toolkit %s: %s", toolkit_slug, exc)
    logger.info(
        "Selected %s Composio toolkits for import (%s managed_by): %s",
        len(items),
        managed_by,
        ", ".join(sorted(item.slug for item in items)),
    )
    return items


async def _sync_native_catalog(
    connector_repository: ConnectorRepository,
    operation_repository: ConnectorOperationRepository,
    trigger_repository: ConnectorTriggerRepository,
    *,
    app_filters: set[str] | None,
    schema_compiler: PydanticCodeSchemaCompiler,
) -> tuple[int, int, int]:
    total_apps = 0
    total_operations = 0
    total_triggers = 0

    # First sync Lemma-managed apps from JSON config (Slack, Jira, Confluence)
    lemma_apps = _load_lemma_apps_config()
    normalized_app_filters = (
        {_normalize_connector_id(slug) for slug in app_filters}
        if app_filters
        else None
    )
    for app_config in lemma_apps:
        app_name = app_config["name"]
        if (
            normalized_app_filters
            and _normalize_connector_id(app_name) not in normalized_app_filters
        ):
            continue

        connector_id = _normalize_connector_id(app_name)
        existing = await connector_repository.get(connector_id)

        auth_method = AuthMethod(app_config.get("auth_method", "OAUTH2"))

        entity = ConnectorEntity(
            id=connector_id,
            title=app_config.get("title"),
            description=app_config.get("description"),
            icon=app_config.get("icon") or (existing.icon if existing else None),
            provider_capabilities=_merge_provider_capabilities(
                existing,
                _lemma_provider_capability(
                    auth_method=auth_method,
                    oauth2_defaults=app_config.get("oauth2_config"),
                    auth_config_schema=app_config.get("auth_config_schema"),
                    credential_schema=app_config.get("credential_schema"),
                    system_oauth=app_config.get("system_oauth"),
                ),
            ),
            agent_instruction=app_config.get("agent_instruction")
            or (existing.agent_instruction if existing else None),
            is_active=app_config.get("is_active", True),
        )
        await _upsert_connector(connector_repository, entity)
        total_apps += 1
        logger.info("Synced Lemma app config: %s", app_name)

        # Sync triggers for this app
        for trigger_data in app_config.get("triggers", []):
            from app.modules.connectors.domain.connector_trigger import (
                ConnectorTriggerEntity,
            )

            existing_trigger = (
                await trigger_repository.get_by_connector_provider_and_name(
                    connector_id,
                    AuthProvider.LEMMA.value,
                    trigger_data["event_type"],
                )
            )
            trigger_entity = ConnectorTriggerEntity(
                id=(
                    existing_trigger.id
                    if existing_trigger
                    else _trigger_id(
                        connector_id, AuthProvider.LEMMA, trigger_data["event_type"]
                    )
                ),
                connector_id=connector_id,
                provider=AuthProvider.LEMMA,
                event_type=trigger_data["event_type"],
                description=trigger_data.get("description"),
                config_schema=trigger_data.get("config_schema"),
                payload_schema=trigger_data.get("payload_schema"),
                payload_example=trigger_data.get("payload_example"),
            )
            if existing_trigger:
                await trigger_repository.update(trigger_entity)
            else:
                await trigger_repository.create(trigger_entity)
            total_triggers += 1

    # Then sync native package apps that still run through Lemma packages.
    for app_slug in _list_native_apps(app_filters):
        connector_id = _normalize_connector_id(app_slug)
        existing = await connector_repository.get(connector_id)

        app_description = None
        app_title = existing.title if existing else None
        operation_descriptors = []
        try:
            info_client = get_native_info_client(connector_id)
            if asyncio.iscoroutine(info_client):
                info_client = await info_client
            metadata = describe_lemma_connector(connector_id)
            app_title = app_title or metadata["title"]
            app_description = metadata["description"]
            if hasattr(info_client, "list_operations"):
                operation_descriptors = await info_client.list_operations()
            elif hasattr(info_client, "list_available_operations"):
                operation_names = await info_client.list_available_operations()
                operation_descriptors = [
                    SimpleNamespace(
                        name=operation_name,
                        **(await info_client.get_operation_details(operation_name)).__dict__,
                    )
                    for operation_name in operation_names
                ]
        except Exception as exc:
            logger.warning(
                "Lemma package unavailable for %s; keeping native app registration without operations: %s",
                connector_id,
                exc,
            )

        entity = ConnectorEntity(
            id=connector_id,
            title=app_title or connector_id.replace("_", " ").title(),
            description=(app_description or (existing.description if existing else None)),
            icon=existing.icon if existing else None,
            provider_capabilities=_merge_provider_capabilities(
                existing,
                _native_package_provider_capability(connector_id, existing),
            ),
            agent_instruction=existing.agent_instruction if existing else None,
            is_active=True,
        )
        await _upsert_connector(connector_repository, entity)
        total_apps += 1

        prepared_operations: list[dict[str, object]] = []
        for descriptor in operation_descriptors:
            operation_name = getattr(descriptor, "name", None)
            if operation_name is None:
                continue
            description = _resolve_operation_description(
                operation_name,
                description=descriptor.description,
            )
            input_schema = (
                descriptor.input_schema()
                if hasattr(descriptor, "input_schema")
                else (
                    schema_compiler.to_json_schema(descriptor.input_schema_content)
                    if getattr(descriptor, "input_schema_content", None)
                    else None
                )
            )
            output_schema = (
                descriptor.output_schema()
                if hasattr(descriptor, "output_schema")
                else (
                    schema_compiler.to_json_schema(descriptor.output_schema_content)
                    if getattr(descriptor, "output_schema_content", None)
                    else None
                )
            )
            prepared_operations.append(
                {
                    "public_name": operation_name,
                    "provider_operation_name": _normalize_operation_name(
                        operation_name
                    ),
                    "display_name": operation_name,
                    "description": description,
                    "input_schema": input_schema,
                    "output_schema": output_schema,
                    "search_document": _build_operation_search_document(
                        public_name=operation_name,
                        display_name=operation_name,
                        description=description,
                    ),
                }
            )

        for operation_data in prepared_operations:
            await _upsert_operation(
                operation_repository,
                connector_id,
                provider=AuthProvider.LEMMA,
                public_name=str(operation_data["public_name"]),
                provider_operation_name=str(operation_data["provider_operation_name"]),
                display_name=operation_data["display_name"],
                description=operation_data["description"],
                input_schema=operation_data["input_schema"],
                output_schema=operation_data["output_schema"],
                search_document=str(operation_data["search_document"]),
            )
            total_operations += 1

    return total_apps, total_operations, total_triggers


async def _sync_composio_catalog(
    connector_repository: ConnectorRepository,
    operation_repository: ConnectorOperationRepository,
    trigger_repository: ConnectorTriggerRepository,
    *,
    app_filters: set[str] | None,
    managed_by: str,
    page_size: int,
    max_composio_apps: int,
) -> tuple[int, int, int]:
    api_key = connector_settings.composio_api_key or os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        logger.info(
            "Skipping Composio catalog sync — COMPOSIO_API_KEY is not set. "
            "Only native apps will be imported."
        )
        return 0, 0, 0

    composio = Composio(api_key=api_key)
    toolkit_items = _list_composio_toolkits(
        composio,
        app_filters=app_filters,
        managed_by=managed_by,
        page_size=page_size,
        max_composio_apps=max_composio_apps,
    )

    total_apps = 0
    total_operations = 0
    total_triggers = 0

    await _deactivate_excluded_composio_connectors(connector_repository)

    for toolkit_item in toolkit_items:
        app_count, operation_count, trigger_count = await _sync_single_composio_toolkit(
            composio,
            toolkit_item,
            connector_repository=connector_repository,
            operation_repository=operation_repository,
            trigger_repository=trigger_repository,
            page_size=page_size,
        )
        total_apps += app_count
        total_operations += operation_count
        total_triggers += trigger_count

    return total_apps, total_operations, total_triggers


async def _sync_single_composio_toolkit(
    composio: Composio,
    toolkit_item,
    *,
    connector_repository: ConnectorRepository,
    operation_repository: ConnectorOperationRepository,
    trigger_repository: ConnectorTriggerRepository,
    page_size: int,
) -> tuple[int, int, int]:
    total_apps = 0
    total_operations = 0
    total_triggers = 0

    connector_id = _resolve_composio_connector_id(toolkit_item.slug)
    supports_native = _uses_native_operations(connector_id)
    existing = await connector_repository.get(connector_id)
    toolkit_detail = composio.toolkits.get(toolkit_item.slug)
    composio_auth_method = _infer_composio_auth_method(toolkit_item, toolkit_detail)
    lemma_capability = None
    if supports_native:
        try:
            lemma_capability = existing.capability_for(AuthProvider.LEMMA) if existing else None
        except ValueError:
            lemma_capability = None
        if lemma_capability is None:
            lemma_capability = _lemma_provider_capability(
                auth_method=_infer_native_auth_method(connector_id, existing),
            )

    entity = ConnectorEntity(
        id=connector_id,
        title=(
            existing.title
            if supports_native and existing
            else getattr(toolkit_item, "name", None)
        ),
        description=(
            existing.description
            if supports_native and existing
            else _toolkit_meta_value(toolkit_item, "description")
        ),
        icon=(
            existing.icon
            if supports_native and existing
            else _toolkit_meta_value(toolkit_item, "logo")
        ),
        provider_capabilities=_merge_provider_capabilities(
            existing,
            _composio_provider_capability(
                auth_method=composio_auth_method,
                toolkit_slug=toolkit_item.slug,
            ),
            lemma_capability,
        ),
        agent_instruction=existing.agent_instruction if existing else None,
        is_active=_is_toolkit_active(toolkit_item),
    )
    await _upsert_connector(connector_repository, entity)
    total_apps += 1

    for tool_index, tool in enumerate(
        _paginate_tools(
            composio,
            toolkit_slug=toolkit_item.slug,
            page_size=page_size,
        ),
        start=1,
    ):
        description = _resolve_operation_description(
            str(tool.slug).strip(),
            description=tool.description,
        )
        await _upsert_operation(
            operation_repository,
            connector_id,
            provider=AuthProvider.COMPOSIO,
            public_name=str(tool.slug).strip(),
            provider_operation_name=_resolve_composio_provider_operation_name(
                tool
            ),
            display_name=tool.name,
            description=description,
            input_schema=tool.input_parameters,
            output_schema=tool.output_parameters,
            search_document=_build_operation_search_document(
                public_name=str(tool.slug).strip(),
                display_name=tool.name,
                description=description,
            ),
            normalize_name=False,
        )
        total_operations += 1
        if tool_index % IMPORT_BATCH_OPERATION_CHUNK_SIZE == 0:
            await operation_repository.session.flush()

    for trigger_index, trigger in enumerate(
        _paginate_triggers(
            composio,
            toolkit_slug=toolkit_item.slug,
            page_size=page_size,
        ),
        start=1,
    ):
        await _upsert_trigger(
            trigger_repository,
            connector_id,
            trigger,
            provider=AuthProvider.COMPOSIO,
        )
        total_triggers += 1
        if trigger_index % IMPORT_BATCH_OPERATION_CHUNK_SIZE == 0:
            await trigger_repository.session.flush()

    return total_apps, total_operations, total_triggers


async def _run_in_session_batch(
    sync_fn: Callable[[ConnectorRepository, ConnectorOperationRepository, ConnectorTriggerRepository], Awaitable[tuple[int, int, int]]],
    *,
    dry_run: bool,
) -> tuple[int, int, int]:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        connector_repository = ConnectorRepository(uow)
        operation_repository = ConnectorOperationRepository(uow)
        trigger_repository = ConnectorTriggerRepository(uow)

        try:
            totals = await sync_fn(
                connector_repository,
                operation_repository,
                trigger_repository,
            )
            if dry_run:
                await uow.rollback()
            else:
                await uow.commit()
            return totals
        except Exception:
            await uow.rollback()
            raise


async def _sync_native_catalog_batched(
    *,
    app_filters: set[str] | None,
    schema_compiler: PydanticCodeSchemaCompiler,
    dry_run: bool,
) -> tuple[int, int, int]:
    total_apps = total_operations = total_triggers = 0

    for app_slug in _list_native_sync_targets(app_filters):
        logger.info("Importing native app batch: %s", app_slug)
        app_count, operation_count, trigger_count = await _run_in_session_batch(
            lambda connector_repository, operation_repository, trigger_repository: _sync_native_catalog(
                connector_repository,
                operation_repository,
                trigger_repository,
                app_filters={app_slug},
                schema_compiler=schema_compiler,
            ),
            dry_run=dry_run,
        )
        total_apps += app_count
        total_operations += operation_count
        total_triggers += trigger_count

    return total_apps, total_operations, total_triggers


async def _sync_composio_catalog_batched(
    *,
    app_filters: set[str] | None,
    managed_by: str,
    page_size: int,
    max_composio_apps: int,
    dry_run: bool,
) -> tuple[int, int, int]:
    api_key = connector_settings.composio_api_key or os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        logger.info(
            "Skipping Composio catalog sync — COMPOSIO_API_KEY is not set. "
            "Only native apps will be imported."
        )
        return 0, 0, 0

    composio = Composio(api_key=api_key)
    toolkit_items = _list_composio_toolkits(
        composio,
        app_filters=app_filters,
        managed_by=managed_by,
        page_size=page_size,
        max_composio_apps=max_composio_apps,
    )

    total_apps = total_operations = total_triggers = 0
    await _run_in_session_batch(
        _deactivate_excluded_composio_connectors_batch,
        dry_run=dry_run,
    )
    for toolkit_item in toolkit_items:
        logger.info("Importing Composio app batch: %s", toolkit_item.slug)
        app_count, operation_count, trigger_count = await _run_in_session_batch(
            lambda connector_repository, operation_repository, trigger_repository: _sync_single_composio_toolkit(
                composio,
                toolkit_item,
                connector_repository=connector_repository,
                operation_repository=operation_repository,
                trigger_repository=trigger_repository,
                page_size=page_size,
            ),
            dry_run=dry_run,
        )
        total_apps += app_count
        total_operations += operation_count
        total_triggers += trigger_count

    return total_apps, total_operations, total_triggers


SKILLS_DIR = Path(__file__).parent.parent / "app" / "modules" / "connectors" / "skills"

def _build_skill_prompt(app_id: str, title: str, description: str, operations: list) -> str:
    """Build the complete LLM prompt for skill doc generation as one plain string."""
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

        field_block = "\n".join(fields) if fields else "  (no schema available)"
        ops_info.append(
            f"Operation: {op_name}\n"
            f"Display: {op_display}\n"
            f"Description: {op_desc}\n"
            f"Input fields (* = required):\n{field_block}"
        )

    ops_block = "\n\n".join(ops_info) if ops_info else "(no operations available)"
    app_desc = (description[:400] if description else f"Connector with {title or app_id}.").strip()

    example_cmd = "lemma connectors operations execute " + app_id + ' OPERATION_NAME --json \'{"payload": {"field1": "value1"}}\''

    return (
        "Write a skill guide for an AI agent. The entire document must be 300-500 words — concise and scannable.\n\n"
        "RULES:\n"
        "- Cover 5-7 of the most common real-world tasks for this platform. No more.\n"
        "- Each task: one sentence explaining when to use it, then the exact CLI command.\n"
        "- Use real field names from the schemas. Use realistic values (real emails, dates, text). No placeholders.\n"
        "- Skip rare, admin, or meta operations.\n"
        "- Output ONLY the markdown. No intro text, no code fence around the whole document.\n\n"
        "FORMAT (follow exactly):\n\n"
        f"# {title or app_id}\n\n"
        f"[1-2 sentences: what {title or app_id} does and who uses it]\n\n"
        f"**Auth config name:** `{app_id}`\n\n"
        "## Common Tasks\n\n"
        "### [Action verb phrase]\n"
        "[1 sentence: when/why]\n"
        "```\n"
        f"{example_cmd}\n"
        "```\n\n"
        "[repeat for each task — aim for 5-7 total]\n\n"
        "## Tips\n"
        f"- `lemma connectors operations search {app_id} <query>` — find more operations\n"
        f"- `lemma connectors operations details {app_id} <OPERATION>` — see full input schema\n\n"
        "---\n\n"
        f"APP: {title or app_id}\n"
        f"APP ID (use in all commands): {app_id}\n"
        f"DESCRIPTION: {app_desc}\n\n"
        f"AVAILABLE OPERATIONS AND INPUT SCHEMAS:\n\n{ops_block}\n\n"
        "Now write the skill guide (300-500 words total)."
    )


async def _generate_skill_doc(
    skill_agent,
    app_id: str,
    title: str,
    description: str,
    operations: list,
    skills_dir: Path,
    *,
    provider: str | None = None,
) -> None:
    prompt = _build_skill_prompt(app_id, title, description, operations)
    try:
        result = await skill_agent.run(prompt)
        if provider:
            skill_file = skills_dir / f"{app_id}.{provider.lower()}.md"
        else:
            skill_file = skills_dir / f"{app_id}.md"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(result.output, encoding="utf-8")
        logger.info("Generated skill doc: %s", skill_file.name)
    except Exception as exc:
        logger.warning("Failed to generate skill for %s (provider=%s): %s", app_id, provider, exc)


def _app_providers(app) -> list[str]:
    """Return list of provider values for a connector."""
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


async def _generate_app_skills(
    skill_agent,
    session,
    app,
    skills_dir: Path,
) -> None:
    from sqlalchemy import select as sa_select
    from app.modules.connectors.infrastructure.models.connector_operation import (
        ConnectorOperation,
    )

    providers = _app_providers(app)
    is_dual = len(providers) >= 2

    async def _for_provider(provider: str | None) -> None:
        stmt = sa_select(ConnectorOperation).where(
            ConnectorOperation.connector_id == app.id
        )
        if provider:
            stmt = stmt.where(ConnectorOperation.provider == provider.upper())
        stmt = stmt.limit(15)
        op_result = await session.execute(stmt)
        operations = list(op_result.scalars().all())
        await _generate_skill_doc(
            skill_agent,
            app_id=app.id,
            title=app.title or app.id,
            description=app.description or "",
            operations=operations,
            skills_dir=skills_dir,
            provider=provider if is_dual else None,
        )

    if is_dual:
        for p in providers:
            await _for_provider(p)
    else:
        await _for_provider(None)



_SKILL_MODEL = "accounts/fireworks/models/deepseek-v4-pro"
_SKILL_BASE_URL = "https://api.fireworks.ai/inference/v1"


def _build_skill_agent():
    from pydantic_ai import Agent as PydanticAIAgent
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    api_key = settings.lemma_openai_api_key or os.environ.get("FIREWORKS_API_KEY")
    if not api_key:
        raise SystemExit("Set lemma_openai_api_key or FIREWORKS_API_KEY to generate skills.")

    client = AsyncOpenAI(base_url=_SKILL_BASE_URL, api_key=api_key)
    return PydanticAIAgent(OpenAIChatModel(_SKILL_MODEL, provider=OpenAIProvider(openai_client=client)))


async def _generate_all_skills(app_filters: set[str] | None = None) -> None:
    try:
        skill_agent = _build_skill_agent()
    except ImportError as exc:
        raise SystemExit("pydantic_ai is required for --generate-skills") from exc

    async with async_session_maker() as session:
        from sqlalchemy import select as sa_select
        from app.modules.connectors.infrastructure.models.connector import Connector

        stmt = sa_select(Connector).where(Connector.is_active.is_(True))
        result = await session.execute(stmt)
        apps = list(result.scalars().all())

        if app_filters:
            apps = [a for a in apps if a.id in app_filters]

        logger.info("Generating skill docs for %d apps...", len(apps))

        batch_size = 5
        for i in range(0, len(apps), batch_size):
            batch = apps[i : i + batch_size]
            await asyncio.gather(*[_generate_app_skills(skill_agent, session, app, SKILLS_DIR) for app in batch])
            logger.info("Completed batch %d/%d", min(i + batch_size, len(apps)), len(apps))

        logger.info("Skill generation complete.")


async def main() -> None:
    args = _parse_args()
    app_filters = {
        app_slug.strip() for app_slug in (args.apps or []) if app_slug.strip()
    } or None
    schema_compiler = PydanticCodeSchemaCompiler()
    native_apps = native_operations = native_triggers = 0
    composio_apps = composio_operations = composio_triggers = 0

    native_sync_targets = set(_list_native_sync_targets(None))
    native_filter_apps = (
        {
            _normalize_connector_id(app_id)
            for app_id in (app_filters or set())
            if _normalize_connector_id(app_id) in native_sync_targets
        }
        if app_filters
        else None
    )

    if args.provider in {"all", "native"}:
        (
            native_apps,
            native_operations,
            native_triggers,
        ) = await _sync_native_catalog_batched(
            app_filters=app_filters,
            schema_compiler=schema_compiler,
            dry_run=args.dry_run,
        )
    elif args.provider == "composio" and native_filter_apps:
        (
            native_apps,
            native_operations,
            native_triggers,
        ) = await _sync_native_catalog_batched(
            app_filters=native_filter_apps,
            schema_compiler=schema_compiler,
            dry_run=args.dry_run,
        )

    if args.provider in {"all", "composio"}:
        (
            composio_apps,
            composio_operations,
            composio_triggers,
        ) = await _sync_composio_catalog_batched(
            app_filters=app_filters,
            managed_by=args.managed_by,
            page_size=args.page_size,
            max_composio_apps=args.max_composio_apps,
            dry_run=args.dry_run,
        )

    if args.dry_run:
        logger.info(
            "Dry run complete: native=%s apps/%s operations/%s triggers, composio=%s apps/%s operations/%s triggers",
            native_apps,
            native_operations,
            native_triggers,
            composio_apps,
            composio_operations,
            composio_triggers,
        )
        return

    logger.info(
        "Imported native=%s apps/%s operations/%s triggers, composio=%s apps/%s operations/%s triggers",
        native_apps,
        native_operations,
        native_triggers,
        composio_apps,
        composio_operations,
        composio_triggers,
    )

    if getattr(args, "generate_skills", False):
        await _generate_all_skills(app_filters=app_filters)


if __name__ == "__main__":
    asyncio.run(main())
