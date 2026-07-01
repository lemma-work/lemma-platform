"""GitHub side of the share loop: publish a pod as a repo, and import a pod
from one — the durable distribution channel (a repo's README badge outlives
any single share message).

  POST /pods/{pod_id}/export/github        -> publish this pod to a new repo
  POST /imports/from-github/{owner}/{repo}  -> create a new pod from a repo

Publish goes through the GitHub connector Lemma already provides (Composio),
so there is no bespoke OAuth here — same account/consent flow as any other
connector. Import fetches a repo's zipball directly (no auth needed for a
public repo) and reuses the exact "create a new pod from a bundle" path the
upload and shared-link flows already use.
"""

from __future__ import annotations

import base64
import re
import zipfile
from io import BytesIO
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.core.crypto import get_secret_cipher
from app.modules.connectors.api.dependencies import ConnectorOperationUseCasesDep
from app.modules.connectors.domain.errors import (
    ConnectorDomainError,
    OperationExecutionAccessDeniedError,
    OperationExecutionError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.pod.api.dependencies import PodServiceDep
from app.modules.pod_import.api.controllers.import_controller import (
    _create_new_pod_from_bundle,
)
from app.modules.pod_import.api.dependencies import ImportAppServiceDep
from app.modules.pod_import.api.schemas import PodImportResponse
from app.modules.pod_import.infrastructure.exporter import BundleExporter

github_publish_router = APIRouter(prefix="/pods/{pod_id}/export", tags=["imports"])
github_import_router = APIRouter(prefix="/imports/from-github", tags=["imports"])
# "Create a new pod" GitHub import lives at /imports/from-github (no target pod
# yet, mirrors new_pod_import_router in import_controller.py); "install into
# this pod" is the pod-scoped router below (mirrors import_controller.router).
github_import_into_pod_router = APIRouter(
    prefix="/pods/{pod_id}/imports/from-github", tags=["imports"]
)

# Composio's action-catalog names for GitHub's create-repo and
# create-or-update-file-contents endpoints. Composio derives these 1:1 from
# GitHub's own OpenAPI operationIds; unlike the rest of this module they are
# NOT verified against a live catalog (this environment has no synced GitHub
# connector operations to check against) — if Composio's naming differs,
# publish fails with a clear message rather than silently mis-publishing.
_OP_CREATE_REPO = "GITHUB_CREATE_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER"
_OP_CREATE_FILE = "GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS"

_GITHUB_ZIPBALL_TIMEOUT_SECONDS = 30.0


class GithubPublishRequest(BaseModel):
    repo_name: str | None = None
    private: bool = False


class GithubPublishResponse(BaseModel):
    status: Literal["published", "not_connected", "failed"]
    repo_url: str | None = None
    import_badge_markdown: str | None = None
    message: str | None = None


class GithubPublishPreviewResponse(BaseModel):
    repo_name: str
    readme: str


def _github_repo_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return (slug or "lemma-pod")[:100]


def _archive_entries(archive: bytes) -> list[tuple[str, bytes]]:
    """Every file in the export archive as (path, content) — pushed to GitHub
    as individual commits so the repo is a real, browsable file tree (and so
    import_from_github can read pod.json etc. straight from it)."""
    entries = []
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entries.append((info.filename, zf.read(info)))
    return entries


def _strip_bundle_root(pod_name: str, entries: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Drop the export archive's own top-level ``<pod_name>/`` wrapper so the
    repo's root IS the bundle root — pod.json, tables/, etc. land directly in
    the repo next to README.md. Without this, the repo carries a redundant
    "<pod_name>/" folder (confusing to browse), and a GitHub codeload zipball
    wraps it *again* on re-fetch, nesting pod.json two folders deep — which
    used to make a published repo fail to re-import with an empty plan."""
    prefix = f"{pod_name}/"
    return [
        (path[len(prefix) :] if path.startswith(prefix) else path, content)
        for path, content in entries
    ]


def _capability_bullets(archive: bytes) -> list[str]:
    """A short "this pod includes" list, counted straight from the archive's
    top-level resource directories — no need to re-stage the bundle."""
    kinds = ("tables", "functions", "agents", "workflows", "schedules", "surfaces", "apps")
    counts: dict[str, set[str]] = {k: set() for k in kinds}
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        for entry in zf.namelist():
            parts = entry.strip("/").split("/")
            # tolerate a wrapping top-level folder (export archives have one)
            for i, part in enumerate(parts):
                if part in kinds and i + 1 < len(parts) and parts[i + 1]:
                    counts[part].add(parts[i + 1])
    labels = {
        "tables": "table",
        "functions": "function",
        "agents": "agent",
        "workflows": "workflow",
        "schedules": "schedule",
        "surfaces": "surface",
        "apps": "app",
    }
    bullets = []
    for kind in kinds:
        n = len(counts[kind])
        if n:
            bullets.append(f"- {n} {labels[kind]}{'s' if n != 1 else ''}")
    return bullets


def _render_readme(pod_name: str, description: str | None, archive: bytes, import_url: str) -> str:
    bullets = _capability_bullets(archive)
    lines = [
        f"# {pod_name}",
        "",
        description or f"A pod built with [Lemma]({import_url.rsplit('/import', 1)[0]}).",
        "",
        f"[![Import to Lemma](https://img.shields.io/badge/Import-Lemma-black)]({import_url})",
        "",
    ]
    if bullets:
        lines += ["## This pod includes", "", *bullets, ""]
    return "\n".join(lines)


def _extract_result(result: Any) -> dict[str, Any]:
    """Composio's execute() result envelope isn't verified against a live
    catalog here; tolerate the common shapes (raw dict, or {"data": {...}})."""
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict):
            return data
        return result
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="GitHub operation returned an unexpected response shape.",
    )


def _is_payload_too_large(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "413" in text or "entity too large" in text or "payload too large" in text


# Composio's own gateway rejects a request above some request-size ceiling
# that isn't documented or discoverable ahead of time (a live publish 413'd on
# a whole app bundle, and again on an individual large file). Rather than
# guess a fixed limit, a large file is split into chunks small enough to fit,
# starting from a conservative guess and adaptively halving on a 413 — this
# converges on a safe size without needing to know the real ceiling, and
# scales to however large a bundle's app assets or seed data get.
_INITIAL_CHUNK_BYTES = 150_000
_MIN_CHUNK_BYTES = 8_000
_CHUNK_SUFFIX_RE = re.compile(r"^(?P<base>.+)\.chunk(?P<index>\d{4})of(?P<total>\d{4})$")


async def _put_file(
    use_cases: Any, common: dict[str, Any], owner: str, repo: str, path: str, content: bytes
) -> None:
    await use_cases.execute_operation_for_auth_config(
        operation_name=_OP_CREATE_FILE,
        payload={
            "owner": owner,
            "repo": repo,
            "path": path,
            "message": f"Add {path}",
            "content": base64.b64encode(content).decode("ascii"),
        },
        **common,
    )


def _split_into_chunks(content: bytes, chunk_size: int) -> list[bytes]:
    return [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)] or [b""]


async def _push_one_file_best_effort(
    use_cases: Any, common: dict[str, Any], owner: str, repo: str, path: str, content: bytes
) -> bool:
    """Push one file, falling back to adaptively-sized `.chunkNNNNofMMMM`
    pieces if it (or a chunk of it) is too big for one request. Returns False
    (skip, don't abort the publish) only once even the smallest chunk size
    still doesn't fit."""
    try:
        await _put_file(use_cases, common, owner, repo, path, content)
        return True
    except OperationExecutionError as exc:
        if not _is_payload_too_large(exc):
            raise

    chunk_size = min(len(content), _INITIAL_CHUNK_BYTES) or _INITIAL_CHUNK_BYTES
    while chunk_size >= _MIN_CHUNK_BYTES:
        chunks = _split_into_chunks(content, chunk_size)
        total = len(chunks)
        try:
            for i, chunk in enumerate(chunks):
                await _put_file(
                    use_cases, common, owner, repo, f"{path}.chunk{i:04d}of{total:04d}", chunk
                )
            return True
        except OperationExecutionError as exc:
            if not _is_payload_too_large(exc):
                raise
            chunk_size //= 2
    return False


async def _push_files_best_effort(
    use_cases: Any,
    common: dict[str, Any],
    owner: str,
    repo: str,
    files: list[tuple[str, bytes]],
) -> list[str]:
    """Commit each file individually (chunking any that don't fit in one
    request); a file that's too large even at the smallest chunk size is
    skipped rather than aborting the whole publish — better a repo missing one
    oversized file than no repo."""
    skipped: list[str] = []
    for path, content in files:
        if not await _push_one_file_best_effort(use_cases, common, owner, repo, path, content):
            skipped.append(path)
    return skipped


def _reassemble_chunked_entries(archive: bytes) -> bytes:
    """A repo published by Lemma may have large files split into
    `<path>.chunkNNNNofMMMM` pieces (see _push_one_file_best_effort) — glue
    each complete set back into its original file before staging. An
    incomplete set (a stale leftover from a chunk size that got shrunk
    mid-publish) is dropped rather than reassembled wrong; missing one
    non-essential file degrades the same way a skipped one does on publish."""
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        chunk_groups: dict[tuple[str, int], dict[int, bytes]] = {}
        passthrough: dict[str, bytes] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            match = _CHUNK_SUFFIX_RE.match(info.filename)
            if match:
                key = (match.group("base"), int(match.group("total")))
                chunk_groups.setdefault(key, {})[int(match.group("index"))] = zf.read(info)
            else:
                passthrough[info.filename] = zf.read(info)

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in passthrough.items():
            zf.writestr(name, content)
        for (base, total), parts in chunk_groups.items():
            if len(parts) == total:
                zf.writestr(base, b"".join(parts[i] for i in range(total)))
    return out.getvalue()


_MAX_REPO_NAME_ATTEMPTS = 5


async def _create_repo_with_retry(
    use_cases: Any,
    common: dict[str, Any],
    repo_name: str,
    *,
    description: str,
    private: bool,
) -> dict[str, Any]:
    """Create the GitHub repo, retrying with a numbered suffix if the name is
    already taken on that account — the same collision every publish can hit
    since Composio has no "create if available" mode, mirroring how a
    duplicate pod name gets a "(copy)" suffix on the create-new-pod path."""
    for attempt in range(1, _MAX_REPO_NAME_ATTEMPTS + 1):
        name = repo_name if attempt == 1 else f"{repo_name}-{attempt}"
        try:
            created = await use_cases.execute_operation_for_auth_config(
                operation_name=_OP_CREATE_REPO,
                payload={
                    "name": name,
                    "description": description,
                    "private": private,
                    "auto_init": False,
                },
                **common,
            )
        except OperationExecutionError as exc:
            if "already exists" in str(exc).lower() and attempt < _MAX_REPO_NAME_ATTEMPTS:
                continue
            raise
        return _extract_result(created.result)
    raise OperationExecutionError(  # pragma: no cover - loop always returns or raises above
        f"Could not find an available repo name starting from '{repo_name}'."
    )


@github_publish_router.get("/github/preview", response_model=GithubPublishPreviewResponse)
async def preview_github_publish(
    pod_id: UUID,
    user: CurrentUser,
    pod_service: PodServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    repo_name: str | None = Query(None),
) -> GithubPublishPreviewResponse:
    """What Publish will actually write, without touching GitHub: the repo name
    it'll create and the exact README it'll commit — so the share dialog can
    show it before the user commits to publishing. Skips row data (with_data=
    False) since the README only ever reports resource *counts*, not rows."""
    pod = await pod_service.get_pod(pod_id, user.id)
    if pod is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pod not found")

    pod_name, archive = await BundleExporter(uow).export(
        pod_id=pod_id, user_id=user.id, ctx=ctx, with_data=False
    )
    slug = _github_repo_slug(repo_name or pod_name or pod.name)

    account_repo = AccountRepository(uow, encryption=get_secret_cipher())
    account = await account_repo.get_by_user_org_and_app(user.id, pod.organization_id, "github")
    owner = account.provider_account_id if account else "your-username"

    import_url = f"https://lemma.work/import/github/{owner}/{slug}"
    readme = _render_readme(pod.name, pod.description, archive, import_url)
    return GithubPublishPreviewResponse(repo_name=slug, readme=readme)


@github_publish_router.post("/github", response_model=GithubPublishResponse)
async def publish_pod_to_github(
    pod_id: UUID,
    user: CurrentUser,
    pod_service: PodServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    use_cases: ConnectorOperationUseCasesDep,
    request: Request,
    body: GithubPublishRequest,
) -> GithubPublishResponse:
    """Publish this pod as a new GitHub repo: bundle + a generated README with
    an import badge. Requires the caller to have already connected GitHub
    (Connectors settings) — this never initiates OAuth itself."""
    pod = await pod_service.get_pod(pod_id, user.id)
    if pod is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pod not found")

    account_repo = AccountRepository(uow, encryption=get_secret_cipher())
    account = await account_repo.get_by_user_org_and_app(user.id, pod.organization_id, "github")
    if account is None:
        return GithubPublishResponse(
            status="not_connected",
            message="Connect GitHub in this pod's Connectors settings, then try again.",
        )

    pod_name, archive = await BundleExporter(uow).export(
        pod_id=pod_id, user_id=user.id, ctx=ctx, with_data=True
    )
    repo_name = _github_repo_slug(body.repo_name or pod_name or pod.name)

    common: dict[str, Any] = dict(
        organization_id=pod.organization_id,
        auth_config_name="github",
        user_id=user.id,
        request=request,
    )
    try:
        repo = await _create_repo_with_retry(
            use_cases,
            common,
            repo_name,
            description=pod.description or f"A pod built with Lemma: {pod.name}",
            private=body.private,
        )
        full_name = repo.get("full_name") or f"{repo.get('owner', {}).get('login', '')}/{repo_name}"
        html_url = repo.get("html_url") or f"https://github.com/{full_name}"
        owner, repo_slug = full_name.split("/", 1)

        import_url = f"https://lemma.work/import/github/{owner}/{repo_slug}"
        readme = _render_readme(pod.name, pod.description, archive, import_url)
        # One file per commit, not the whole bundle as a single base64 blob —
        # a real bundle can exceed Composio's request-size ceiling as one
        # payload (a live publish hit a 413 pushing the zip whole), and a
        # committed file tree is what makes the repo importable in the first
        # place (import_from_github reads pod.json etc. from the repo's own
        # tree, not from a zip nested inside it). The ceiling's exact size is
        # unknown (it's Composio's own gateway, not GitHub's — a live publish
        # still hit 413 on an individual file after that first fix), so a
        # single oversized file is skipped rather than failing the whole
        # publish; the rest of the bundle still lands.
        files = [
            ("README.md", readme.encode("utf-8")),
            *_strip_bundle_root(pod_name, _archive_entries(archive)),
        ]
        skipped = await _push_files_best_effort(use_cases, common, owner, repo_slug, files)
        if any(path.rsplit("/", 1)[-1] == "pod.json" for path in skipped):
            raise OperationExecutionError(
                "pod.json itself was too large to publish — the repo was created but "
                "isn't importable. This shouldn't happen for a normal pod; please retry."
            )
    except (OperationExecutionUnauthorizedError, OperationExecutionAccessDeniedError):
        return GithubPublishResponse(
            status="not_connected",
            message="Your GitHub connection needs to be reconnected — check Connectors settings.",
        )
    except (OperationExecutionError, ConnectorDomainError) as exc:
        return GithubPublishResponse(status="failed", message=str(exc))

    message = (
        f"{len(skipped)} file(s) were too large to publish and were skipped: "
        f"{', '.join(skipped)}"
        if skipped
        else None
    )
    return GithubPublishResponse(
        status="published",
        repo_url=html_url,
        import_badge_markdown=f"[![Import to Lemma](https://img.shields.io/badge/Import-Lemma-black)]({import_url})",
        message=message,
    )


@github_import_router.post(
    "/{owner}/{repo}", response_model=PodImportResponse, status_code=status.HTTP_201_CREATED
)
async def import_from_github(
    owner: str,
    repo: str,
    user: CurrentUser,
    pod_service: PodServiceDep,
    service: ImportAppServiceDep,
    uow: UoWDep,
    organization_id: UUID,
    ref: str = "HEAD",
) -> PodImportResponse:
    """Create a new pod from a public GitHub repo's bundle — the engine behind
    a repo's "Import to Lemma" badge. Fetches the repo's zipball directly
    (works for any public repo with no auth); private repos are a follow-up
    once resolving the *viewer's* own GitHub account is wired in."""
    archive = _reassemble_chunked_entries(await _fetch_repo_zip(owner, repo, ref))
    entity = await _create_new_pod_from_bundle(
        pod_service=pod_service,
        service=service,
        organization_id=organization_id,
        user_id=user.id,
        archive=archive,
        filename=f"{repo}.zip",
        source_kind="github",
        source_ref=f"{owner}/{repo}",
    )
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)


@github_import_into_pod_router.post(
    "/{owner}/{repo}", response_model=PodImportResponse, status_code=status.HTTP_201_CREATED
)
async def import_from_github_into_pod(
    pod_id: UUID,
    owner: str,
    repo: str,
    user: CurrentUser,
    service: ImportAppServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    ref: str = "HEAD",
) -> PodImportResponse:
    """Install a public GitHub repo's bundle into an EXISTING pod — the
    "install into this pod" counterpart to import_from_github's "create a new
    pod" path. Same fetch as import_from_github; plans into pod_id the same
    way create_import (import_controller.py) does for an uploaded bundle."""
    archive = _reassemble_chunked_entries(await _fetch_repo_zip(owner, repo, ref))
    entity = await service.create(
        pod_id=pod_id,
        user_id=user.id,
        archive=archive,
        filename=f"{repo}.zip",
        source_name=f"{owner}/{repo}",
    )
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)


async def _fetch_repo_zip(owner: str, repo: str, ref: str) -> bytes:
    zip_url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"
    async with httpx.AsyncClient(timeout=_GITHUB_ZIPBALL_TIMEOUT_SECONDS) as client:
        resp = await client.get(zip_url, follow_redirects=True)
    if resp.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{owner}/{repo} isn't a public GitHub repo (or doesn't exist).",
        )
    resp.raise_for_status()
    return resp.content
