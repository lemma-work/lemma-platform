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
import json
import re
import zipfile
from collections.abc import AsyncGenerator
from io import BytesIO
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.core.config import settings
from app.core.crypto import get_secret_cipher
from app.core.log.log import get_logger
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
from app.modules.pod_import.infrastructure.ai_readme import polish_available, polish_readme
from app.modules.pod_import.infrastructure.exporter import BundleExporter
from app.modules.pod_import.infrastructure.readme import (
    import_badge_markdown,
    render_readme,
    resource_counts,
)

logger = get_logger(__name__)

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
    # A user-edited README from the share dialog. When non-empty it is
    # committed verbatim (no render, no AI polish) — except any import URL in
    # it gets rewritten to the final repo slug, which a name collision can
    # change server-side after the preview guessed one. Capped well under the
    # ~150KB Composio single-request ceiling: a bigger file would silently be
    # split into .chunkNNNN pieces, leaving the repo with no rendered README
    # (and no import badge) at all.
    readme: str | None = Field(default=None, max_length=100_000)


class GithubPublishResponse(BaseModel):
    status: Literal["published", "not_connected", "failed"]
    repo_url: str | None = None
    import_badge_markdown: str | None = None
    # The exact README text committed to the repo (post AI polish / post
    # import-URL rewrite) so the UI can show what actually landed — None
    # unless the publish got far enough to write one.
    readme: str | None = None
    message: str | None = None


class GithubPublishPreviewResponse(BaseModel):
    repo_name: str
    readme: str
    # Non-zero bundle resource counts, e.g. {"tables": 5, "agents": 1} — the
    # share dialog's summary of what publish will ship.
    resource_counts: dict[str, int]
    # True when publish will run the system-LLM polish pass over the README
    # draft — the share dialog labels the publish step accordingly. Preview
    # itself stays deterministic (never calls the model).
    ai_polish: bool


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


def _frontend_base() -> str:
    return settings.frontend_url.rstrip("/")


def _rewrite_import_urls(text: str, import_url: str, requested_slug: str) -> str:
    """Repoint an override README's import link(s) at the repo that actually
    got created: the preview embeds a URL guessed from the requested owner/
    slug, but a name collision makes _create_repo_with_retry land on a
    ``-N``-suffixed repo. Only URLs ending in the slug this publish asked for
    are rewritten — a deliberate link to some *other* pod's import page, or
    punctuation right after the URL, must survive verbatim."""
    # The trailing guard is two lookaheads because "." is both a legal slug
    # character and sentence punctuation: "…/trumpet. Enjoy!" must rewrite
    # (dot ends the sentence), while "…/trumpet-2" and "…/trumpet.zip" are
    # different slugs and must not.
    pattern = (
        rf"{re.escape(_frontend_base())}/import/github/[A-Za-z0-9_.-]+/"
        rf"{re.escape(requested_slug)}(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_-])"
    )
    # Lambda replacement: import_url is literal text, not a template re.sub
    # should expand backslashes in.
    return re.sub(pattern, lambda _match: import_url, text)


def _progress_line(**fields: Any) -> str:
    return json.dumps({"event": "progress", **fields}) + "\n"


def _result_line(result: GithubPublishResponse) -> str:
    return json.dumps({"event": "result", **result.model_dump(mode="json")}) + "\n"


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
    it'll create and the README draft it'll commit — so the share dialog can
    show it before the user commits to publishing. Skips row data (with_data=
    False): the renderer then omits the seed-row column, and re-exporting every
    row just to preview a README would be waste. Deterministic — the optional
    AI polish (flagged via ``ai_polish``) only runs on publish."""
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

    import_url = f"{_frontend_base()}/import/github/{owner}/{slug}"
    readme = render_readme(pod.name, pod.description, archive, import_url, _frontend_base())
    return GithubPublishPreviewResponse(
        repo_name=slug,
        readme=readme,
        resource_counts=resource_counts(archive),
        ai_polish=polish_available(),
    )


@github_publish_router.post("/github", response_class=StreamingResponse)
async def publish_pod_to_github(
    pod_id: UUID,
    user: CurrentUser,
    pod_service: PodServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    use_cases: ConnectorOperationUseCasesDep,
    request: Request,
    body: GithubPublishRequest,
) -> StreamingResponse:
    """Publish this pod as a new GitHub repo: bundle + a generated README with
    an import badge. Requires the caller to have already connected GitHub
    (Connectors settings) — this never initiates OAuth itself.

    Streams NDJSON: ``{"event":"progress",...}`` lines (stage export/repo/
    readme, then one per file upload with done/total/path) and a final
    ``{"event":"result",...}`` line carrying exactly the GithubPublishResponse
    fields. A publish is dozens of sequential Composio calls, easily a minute —
    without progress the dialog is a dead spinner. Errors after streaming
    starts can't become HTTP errors anymore, so they map onto the same result
    statuses the old JSON response used (the missing-connection case is also a
    result line, so the frontend has one parsing path)."""
    pod = await pod_service.get_pod(pod_id, user.id)
    if pod is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pod not found")

    account_repo = AccountRepository(uow, encryption=get_secret_cipher())
    account = await account_repo.get_by_user_org_and_app(user.id, pod.organization_id, "github")

    common: dict[str, Any] = dict(
        organization_id=pod.organization_id,
        auth_config_name="github",
        user_id=user.id,
        request=request,
    )

    # The generator runs after this handler returns, but it can keep using the
    # request-scoped uow: FastAPI keeps yield-dependencies (and their pooled
    # connection) alive for the whole StreamingResponse body (same lifecycle
    # the SSE endpoints in conversation_controller.py rely on). Pinning one
    # connection for a one-shot publish is fine; the long part (Composio
    # calls) doesn't touch the DB anyway.
    async def publish_events() -> AsyncGenerator[str, None]:
        if account is None:
            yield _result_line(
                GithubPublishResponse(
                    status="not_connected",
                    message="Connect GitHub in this pod's Connectors settings, then try again.",
                )
            )
            return
        try:
            yield _progress_line(stage="export", label="Bundling pod")
            pod_name, archive = await BundleExporter(uow).export(
                pod_id=pod_id, user_id=user.id, ctx=ctx, with_data=True
            )
            repo_name = _github_repo_slug(body.repo_name or pod_name or pod.name)

            yield _progress_line(stage="repo", label="Creating repository")
            repo = await _create_repo_with_retry(
                use_cases,
                common,
                repo_name,
                description=pod.description or f"A pod built with Lemma: {pod.name}",
                private=body.private,
            )
            full_name = (
                repo.get("full_name") or f"{repo.get('owner', {}).get('login', '')}/{repo_name}"
            )
            html_url = repo.get("html_url") or f"https://github.com/{full_name}"
            owner, repo_slug = full_name.split("/", 1)

            import_url = f"{_frontend_base()}/import/github/{owner}/{repo_slug}"
            override = body.readme if body.readme and body.readme.strip() else None
            ai_polish = override is None and polish_available()
            yield _progress_line(
                stage="readme",
                label="Polishing README with AI" if ai_polish else "Writing README",
            )
            if override is not None:
                # The user already saw and edited the draft — commit their text
                # verbatim. Only the import URL may be stale: the preview embeds
                # one guessed from the requested owner/slug, and a repo-name
                # collision just landed the repo on a suffixed slug.
                readme = _rewrite_import_urls(override, import_url, repo_name)
            else:
                readme = render_readme(
                    pod.name, pod.description, archive, import_url, _frontend_base()
                )
                if ai_polish:
                    readme = (
                        await polish_readme(
                            readme,
                            pod_name=pod.name,
                            description=pod.description,
                            user_id=user.id,
                            organization_id=pod.organization_id,
                            pod_id=pod_id,
                        )
                        or readme
                    )
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
            skipped: list[str] = []
            for done, (path, content) in enumerate(files, start=1):
                yield _progress_line(stage="upload", done=done, total=len(files), path=path)
                if not await _push_one_file_best_effort(
                    use_cases, common, owner, repo_slug, path, content
                ):
                    skipped.append(path)
            if any(path.rsplit("/", 1)[-1] == "pod.json" for path in skipped):
                raise OperationExecutionError(
                    "pod.json itself was too large to publish — the repo was created but "
                    "isn't importable. This shouldn't happen for a normal pod; please retry."
                )
        except (OperationExecutionUnauthorizedError, OperationExecutionAccessDeniedError):
            yield _result_line(
                GithubPublishResponse(
                    status="not_connected",
                    message="Your GitHub connection needs to be reconnected — "
                    "check Connectors settings.",
                )
            )
            return
        except (OperationExecutionError, ConnectorDomainError) as exc:
            yield _result_line(GithubPublishResponse(status="failed", message=str(exc)))
            return
        except Exception:
            # Mid-stream there is no 500 to raise; surface a generic failure
            # (never a raw traceback) and keep the details server-side.
            logger.exception("GitHub publish failed unexpectedly for pod %s", pod_id)
            yield _result_line(
                GithubPublishResponse(
                    status="failed",
                    message="Publishing failed unexpectedly — please try again.",
                )
            )
            return

        message = (
            f"{len(skipped)} file(s) were too large to publish and were skipped: "
            f"{', '.join(skipped)}"
            if skipped
            else None
        )
        yield _result_line(
            GithubPublishResponse(
                status="published",
                repo_url=html_url,
                import_badge_markdown=import_badge_markdown(import_url),
                readme=readme,
                message=message,
            )
        )

    return StreamingResponse(publish_events(), media_type="application/x-ndjson")


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
