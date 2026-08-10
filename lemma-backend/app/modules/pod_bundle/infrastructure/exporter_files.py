"""Exporting a pod's file tree into a bundle's ``files/`` directory.

Split out of :mod:`exporter` so the walk and its byte-budgeted download loop are
readable on their own -- and because the file tree is a graph, not a tree, which
is the kind of detail that hides in a thousand-line module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle.layout import _write_json

from app.core.authorization.context import Context
from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

logger = get_logger(__name__)


async def export_pod_files(
    *,
    root: Path,
    uow: SqlAlchemyUnitOfWork,
    pod_id: UUID,
    ctx: Context,
    data_budget: Any,
    warnings: list[str],
    folder_paths: list[str],
) -> bool:
    """Export the named folders into ``files/`` — folders as ``.folder.json``,
    file bytes (drawn from the shared data budget), and a ``.files.json``
    manifest — mirroring the CLI layout so either tool can import the result.
    Returns whether any ``files/`` content was written.

    Only ``folder_paths`` and what lives beneath them are exported: naming a
    folder means that subtree. There is no way to ask for the whole file tree,
    which is what keeps a pod's private files out of a bundle nobody meant to
    put them in.

    Best-effort: a file that can't be listed/downloaded is skipped, never
    failing the export."""
    from lemma_pod_bundle.layout import FILES_MANIFEST

    from app.composition.pod_bundle_resources import build_file_service

    if not folder_paths:
        return False

    try:
        service = build_file_service(uow)
        entities = await _collect_named_folders(
            service, pod_id, ctx, folder_paths, warnings
        )
    except Exception as exc:  # noqa: BLE001 - files are best-effort
        logger.debug('pod_bundle.exporter.skipping_file_export_pod_s.diagnostic', pod_id=pod_id)
        # Best-effort must still be audible: silently returning an export
        # with no `files/` looks identical to a pod that has no files, and
        # the person restoring it only finds out when the files are gone.
        warnings.append(
            f"file export skipped: the pod's file tree could not be read "
            f"({type(exc).__name__})."
        )
        return False

    pod_entities = [
        e
        for e in entities
        if str(getattr(e, "visibility", "") or "").upper() == "POD"
    ]
    if not pod_entities:
        return False

    files_root = root / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    wrote = False

    # Folders first so parent dirs exist before their files land.
    for folder in sorted(
        (e for e in pod_entities if e.is_folder), key=lambda e: str(e.path or "")
    ):
        parts = [p for p in str(folder.path or "").split("/") if p]
        if not parts:
            continue
        target = files_root.joinpath(*parts)
        target.mkdir(parents=True, exist_ok=True)
        _write_json(
            target / ".folder.json",
            {"description": folder.description, "visibility": folder.visibility},
        )
        wrote = True

    file_manifest: list[dict[str, Any]] = []
    for entity in sorted(
        (e for e in pod_entities if e.is_file), key=lambda e: str(e.path or "")
    ):
        path = str(entity.path or "")
        parts = [p for p in path.split("/") if p]
        if not parts:
            continue
        # Pre-check the declared size so an oversized file isn't downloaded
        # just to be rejected.
        declared = int(getattr(entity, "size_bytes", 0) or 0)
        if declared and not data_budget.allow(name=f"files{path}", size=declared):
            continue
        try:
            _entity, content = await service.download_file_content_by_path(
                pod_id, path, ctx
            )
        except Exception as exc:  # noqa: BLE001 - one bad file is not fatal
            warnings.append(f"file '{path}' skipped: {exc}")
            continue
        # When size wasn't known up front, budget the real bytes now.
        if not declared and not data_budget.allow(
            name=f"files{path}", size=len(content)
        ):
            continue
        target = files_root.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        await run_blocking(target.write_bytes, content, limiter="cpu_bound")
        file_manifest.append(
            {
                "path": path,
                "description": entity.description,
                "visibility": entity.visibility,
                "search_enabled": entity.search_enabled,
            }
        )
        wrote = True

    if file_manifest:
        _write_json(files_root / FILES_MANIFEST, {"files": file_manifest})
    return wrote

async def _collect_named_folders(
    service: Any,
    pod_id: UUID,
    ctx: Context,
    folder_paths: list[str],
    warnings: list[str],
) -> list[Any]:
    """Every entity under each named folder, de-duplicated, order preserved.

    A path that is not a folder here is reported and skipped: the caller named
    it deliberately, so silence would leave them believing it travelled.
    """
    entities: list[Any] = []
    seen_paths: set[str] = set()
    for folder_path in folder_paths:
        if folder_path == "/":
            # "/" is the whole file tree, which is the one thing named
            # selection exists to prevent. Refusing it by name stops the
            # blanket export returning through the front door.
            warnings.append(
                "folder '/' is not exportable: name the folders you want "
                "rather than the pod's whole file tree"
            )
            continue
        found = await _folder_entity(service, pod_id, ctx, folder_path)
        if found is None:
            warnings.append(
                f"folder '{folder_path}' requested for export but not found "
                f"in the pod; skipped"
            )
            continue
        subtree = await walk_pod_files(service, pod_id, ctx, folder_path)
        for entity in [found, *subtree]:
            path = str(entity.path or "")
            if path in seen_paths:
                continue
            seen_paths.add(path)
            entities.append(entity)
    return entities


async def _folder_entity(
    service: Any, pod_id: UUID, ctx: Context, folder_path: str
) -> Any | None:
    """The folder entity at ``folder_path``, or None when it isn't one.

    Found by listing the parent rather than the path itself: a folder that lists
    itself would otherwise look indistinguishable from one that does not exist.
    """
    parent = folder_path.rsplit("/", 1)[0] or "/"
    cursor: str | None = None
    while True:
        items, cursor = await service.list_files(
            pod_id, ctx, directory_path=parent, limit=100, cursor=cursor
        )
        for item in items:
            if str(item.path or "") == folder_path and item.is_folder:
                return item
        if not cursor:
            return None


async def walk_pod_files(
    service: Any,
    pod_id: UUID,
    ctx: Context,
    dir_path: str = "/",
    _seen: set[str] | None = None,
) -> list[Any]:
    """Depth-first list of every file/folder entity under ``dir_path``,
    paging each directory fully (the tree endpoint caps files-per-dir, so we
    walk with ``list_files`` instead).

    ``_seen`` makes the walk a graph traversal rather than a tree one. A
    listing is not guaranteed to be strictly downward: a user's home folder
    lists *itself*, which recursed until the interpreter gave up. The
    RecursionError was then swallowed by the best-effort caller, so every
    pod with a home folder at its root -- which is every real pod -- exported
    an empty `files/` and said nothing about it."""
    seen = _seen if _seen is not None else set()
    if dir_path in seen:
        return []
    seen.add(dir_path)

    out: list[Any] = []
    cursor: str | None = None
    while True:
        items, cursor = await service.list_files(
            pod_id, ctx, directory_path=dir_path, limit=100, cursor=cursor
        )
        for item in items:
            child = str(item.path or "")
            if item.is_folder and child in seen:
                continue
            out.append(item)
            if item.is_folder:
                out.extend(
                    await walk_pod_files(
                        service, pod_id, ctx, dir_path=child, _seen=seen
                    )
                )
        if not cursor:
            break
    return out
