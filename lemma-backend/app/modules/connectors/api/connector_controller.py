from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.core.api.dependencies import CurrentUser
from app.modules.connectors.api.dependencies import (
    ConnectorOperationServiceDep,
    ConnectorServiceDep,
)
from app.modules.connectors.api.schemas import (
    ConnectorDetailResponseSchema,
    ConnectorListResponseSchema,
    ConnectorResponseSchema,
    ConnectorSkillResponse,
)

SKILLS_DIR = (Path(__file__).parent.parent / "skills").resolve()

# A connector id is a catalog slug. Constraining it here is what stops a path
# segment like `..` (or a percent-encoded separator) from walking out of the
# skills directory and reading an arbitrary file off the server.
_CONNECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SKILL_KINDS = frozenset({"composio", "package", "http", "sql", "mcp"})


def _resolve_skill_file(connector_id: str, kind: str | None) -> Path | None:
    """Resolve a skill doc: kind-specific, then generic, else None.

    The request never contributes to a path. Both components are validated
    against a fixed shape, and the result is then used to *select* from the
    directory's own listing by name -- so the returned path is always one the
    server enumerated, and traversal has nothing to act on. Building the path
    and re-checking containment afterwards would also be safe today, but only
    for as long as every check stayed correct; selection has nothing to get
    wrong.
    """
    if not _CONNECTOR_ID_RE.match(connector_id or ""):
        return None

    candidates = []
    if kind and kind.lower() in _SKILL_KINDS:
        candidates.append(f"{connector_id}.{kind.lower()}.md")
    candidates.append(f"{connector_id}.md")

    try:
        available = {
            entry.name: entry for entry in SKILLS_DIR.iterdir() if entry.is_file()
        }
    except OSError:
        return None

    for name in candidates:
        found = available.get(name)
        if found is not None:
            return found
    return None


router = APIRouter(prefix="/connectors", tags=["Connectors"])


@router.get(
    "",
    response_model=ConnectorListResponseSchema,
    operation_id="connector.list",
    summary="List Connectors",
    description="Get all active connectors available for connector",
)
async def list_connectors(
    user: CurrentUser,
    connector_service: ConnectorServiceDep,
    limit: int = Query(default=100),
    page_token: str | None = Query(default=None),
) -> ConnectorListResponseSchema:
    connectors, next_cursor = await connector_service.list_connectors(
        limit=limit, cursor=page_token
    )
    return ConnectorListResponseSchema(
        items=[ConnectorResponseSchema.model_validate(app) for app in connectors],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.get(
    "/{connector_id}/skill",
    response_model=ConnectorSkillResponse,
    operation_id="connector.skill.get",
    summary="Get Connector Skill",
    description=(
        "Get the skill guide markdown for a connector. "
        "Pass `kind=package` or `kind=composio` to get kind-specific instructions "
        "when the app supports both. Falls back to the generic doc if no kind-specific file exists. "
        "Returns 404 if no skill doc has been generated yet."
    ),
)
async def get_connector_skill(
    user: CurrentUser,
    connector_id: str,
    connector_service: ConnectorServiceDep,
    kind: str | None = Query(
        default=None, description="Kind override, e.g. package or composio"
    ),
) -> ConnectorSkillResponse:
    skill_file = _resolve_skill_file(connector_id, kind)
    if skill_file is None:
        raise HTTPException(
            status_code=404, detail=f"No skill doc found for '{connector_id}'"
        )
    markdown = skill_file.read_text(encoding="utf-8")
    try:
        connector = await connector_service.get_connector(connector_id)
        title = connector.title
    except Exception:
        title = None
    effective_kind = kind or (
        "package" if f"{connector_id}.package.md" == skill_file.name else None
    )
    return ConnectorSkillResponse(
        connector_id=connector_id,
        title=title,
        markdown=markdown,
        kind=effective_kind,
    )


@router.get(
    "/{connector_id}",
    response_model=ConnectorDetailResponseSchema,
    operation_id="connector.get",
    summary="Get Connector",
    description="Get a specific connector by ID along with its operation catalog",
)
async def get_connector(
    user: CurrentUser,
    connector_id: str,
    connector_service: ConnectorServiceDep,
    operation_service: ConnectorOperationServiceDep,
) -> ConnectorDetailResponseSchema:
    connector = await connector_service.get_connector(connector_id)
    operations = await operation_service.list_operations(connector_id)
    return ConnectorDetailResponseSchema(
        **ConnectorResponseSchema.model_validate(connector).model_dump(),
        operations={operation.name: operation for operation in operations},
    )
