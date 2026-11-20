"""A repository archive narrowed to the folder that holds its bundle.

A repository can hold several bundles, one per folder (``install/mail``,
``install/crm``). A GitHub import that names a folder narrows the fetched
archive to it before staging, so planning and applying read that bundle and
nothing else -- the same archive a repository holding one bundle at its root
would have given them, and the same checks on the way in.
"""

from __future__ import annotations

import io
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from lemma_pod_bundle import POD_MANIFEST_FILE

from app.modules.pod_bundle.domain.errors import GithubImportError

_CHUNK_SIZE = 1024 * 1024


def bundle_in_folder(
    archive: bytes,
    path: str,
    *,
    repository: str,
    max_uncompressed_bytes: int,
) -> bytes:
    """The archive of ``path`` alone, with the folder's contents at its root.

    Fails before anything is planned when the folder is not in the archive
    (404) or holds no ``pod.json`` (422). Members are copied with their names
    below the folder and their modes, so the extraction that follows refuses
    what it always refuses -- a ``..`` name, a symlink.
    """
    try:
        with ZipFile(io.BytesIO(archive)) as source:
            names = source.namelist()
            prefix = _folder_prefix(names, path)
            if prefix is None:
                raise GithubImportError(
                    f"There is no folder '{path}' in {repository}.",
                    code="GITHUB_PATH_NOT_FOUND",
                    status_code=404,
                    details={"path": path},
                )
            if prefix + POD_MANIFEST_FILE not in names:
                raise GithubImportError(
                    f"The folder '{path}' in {repository} has no "
                    f"{POD_MANIFEST_FILE}, so there is no bundle in it to install.",
                    code="GITHUB_PATH_NOT_A_BUNDLE",
                    status_code=422,
                    details={"path": path},
                )
            return _repack(
                source, prefix, limit=max_uncompressed_bytes, label=f"'{path}'"
            )
    except BadZipFile as exc:
        raise GithubImportError(
            "GitHub did not return a readable zip archive.",
            code="GITHUB_ARCHIVE_INVALID",
            status_code=422,
        ) from exc


def _folder_prefix(names: list[str], path: str) -> str | None:
    """Where ``path`` starts in the archive, or ``None`` when it is not there.

    GitHub wraps a repository's archive in one top-level folder
    (``owner-repo-sha/``), so the folder is looked for under that first, then
    at the archive's root.
    """
    tops = {name.split("/", 1)[0] for name in names}
    candidates = [f"{next(iter(tops))}/{path}/"] if len(tops) == 1 else []
    candidates.append(f"{path}/")
    for prefix in candidates:
        if any(name.startswith(prefix) for name in names):
            return prefix
    return None


def _repack(source: ZipFile, prefix: str, *, limit: int, label: str) -> bytes:
    buffer = io.BytesIO()
    written = 0
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as narrowed:
        for info in source.infolist():
            if not info.filename.startswith(prefix) or info.filename == prefix:
                continue
            member = ZipInfo(info.filename[len(prefix) :], date_time=info.date_time)
            member.external_attr = info.external_attr
            member.compress_type = ZIP_DEFLATED
            if info.is_dir():
                narrowed.writestr(member, b"")
                continue
            with source.open(info) as reader, narrowed.open(member, "w") as writer:
                while chunk := reader.read(_CHUNK_SIZE):
                    written += len(chunk)
                    if written > limit:
                        raise GithubImportError(
                            f"The folder {label} exceeds the maximum allowed size.",
                            code="GITHUB_ARCHIVE_TOO_LARGE",
                            status_code=413,
                        )
                    writer.write(chunk)
    return buffer.getvalue()
