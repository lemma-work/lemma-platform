from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lemma_sdk.openapi_client.models.create_app_request import CreateAppRequest

from ..cli_core.dotenv import read_env_file

_REQUIRED_ENV_VARS = (
    "VITE_LEMMA_API_URL",
    "VITE_LEMMA_AUTH_URL",
    "VITE_LEMMA_POD_ID",
)


def resolve_app_project_env(
    source_dir: Path,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for filename in (".env", ".env.local", ".env.production", ".env.production.local"):
        merged.update(read_env_file(source_dir / filename))
    merged.update(os.environ)
    return merged


def _validate_app_env(env: dict[str, str]) -> None:
    missing = [name for name in _REQUIRED_ENV_VARS if not env.get(name)]
    if missing:
        raise ValueError(
            f"Missing required app env vars: {', '.join(missing)}. "
            "Run `lemma apps init` to create .env.local, or add these values "
            "to the app project's environment."
        )


def _detect_package_manager(source_dir: Path) -> str:
    if (source_dir / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
        return "pnpm"
    if (source_dir / "yarn.lock").exists() and shutil.which("yarn"):
        return "yarn"
    return "npm"


def _build_app(
    source_dir: Path,
    env: dict[str, str],
) -> Path:
    package_manager = _detect_package_manager(source_dir)
    command = [package_manager, "run", "build"]
    result = subprocess.run(
        command,
        cwd=source_dir,
        env={
            **os.environ,
            **{
                key: value
                for key, value in env.items()
                if key in _REQUIRED_ENV_VARS or key.startswith("VITE_")
            },
        },
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"{package_manager} run build failed.")
    dist = source_dir / "dist"
    if not (dist / "index.html").exists():
        raise ValueError(f"App build output missing at {dist / 'index.html'}")
    return dist


def _archive_dist_dir(dist_dir: Path) -> str:
    temp_file = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    temp_file.close()
    with ZipFile(temp_file.name, "w", compression=ZIP_DEFLATED) as archive:
        for path in dist_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(dist_dir).as_posix())
    return temp_file.name


def _should_exclude_source_path(path: str) -> bool:
    excluded = {
        "node_modules",
        ".git",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".cache",
        "coverage",
        "__pycache__",
    }
    return any(part in excluded for part in Path(path).parts)


def _archive_source_dir(source_dir: Path) -> str:
    temp_file = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    temp_file.close()
    with ZipFile(temp_file.name, "w", compression=ZIP_DEFLATED) as archive:
        for path in source_dir.rglob("*"):
            if not path.is_file():
                continue
            relative_path = path.relative_to(source_dir)
            if _should_exclude_source_path(relative_path.as_posix()):
                continue
            archive.write(path, arcname=relative_path.as_posix())
    return temp_file.name


def classify_app_source(source: Path) -> str:
    """Classify an app source into a deploy tier.

    - ``"html"``: a single ``.html``/``.htm`` file, wrapped as ``index.html``.
    - ``"static"``: a directory with ``index.html`` and no ``package.json`` —
      a prebuilt/no-build site, uploaded as-is.
    - ``"vite"``: a directory with ``package.json`` — built with the project's
      package manager (the original behavior).

    No-build tiers (``html``/``static``) carry no baked pod context; the host
    injects ``window.__LEMMA_CONFIG__`` at serve time, so the ``VITE_LEMMA_*``
    env requirement does not apply to them.
    """
    source = source.resolve()
    if source.is_file():
        if source.suffix.lower() in {".html", ".htm"}:
            return "html"
        raise ValueError(
            f"App source file must be an .html file, got: {source.name}"
        )
    if not source.is_dir():
        raise ValueError(f"App source does not exist: {source}")
    if (source / "package.json").exists():
        return "vite"
    if (source / "index.html").exists():
        return "static"
    raise ValueError(
        f"App source {source} has no package.json (Vite project) and no "
        "index.html (static site). Provide one of those, or pass a single "
        ".html file."
    )


def _materialize_static_dist(source: Path, tier: str) -> Path:
    """Build a temporary dist directory for a no-build (html/static) source."""
    dist_dir = Path(tempfile.mkdtemp(prefix="lemma-app-dist-"))
    if tier == "html":
        shutil.copyfile(source, dist_dir / "index.html")
        return dist_dir
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(source)
        if _should_exclude_source_path(relative_path.as_posix()):
            continue
        target = dist_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return dist_dir


def deploy_app_bundle(
    client: Any,
    *,
    pod_id: str,
    app_name: str,
    source_dir: Path,
    dist_dir: Path | None = None,
    ensure_exists: bool = False,
) -> dict[str, Any]:
    source = source_dir.resolve()
    tier = classify_app_source(source)

    pod_sdk = client.pod(pod_id)
    try:
        pod_sdk.apps.get(app_name)
    except Exception:
        if ensure_exists:
            pod_sdk.apps.create(
                CreateAppRequest.from_dict({"name": app_name, "public_slug": app_name})
            )

    temp_dist_dir: Path | None = None
    if tier == "vite":
        env = resolve_app_project_env(source)
        _validate_app_env(env)
        if dist_dir is None:
            dist_dir = _build_app(source, env)
        else:
            dist_dir = dist_dir.resolve()
            if not dist_dir.is_dir():
                raise ValueError(f"App dist directory does not exist: {dist_dir}")
    else:
        # No-build tiers: the host injects pod context at serve time, so there
        # is no env to validate and nothing to build.
        if dist_dir is not None:
            raise ValueError("--dist-dir is only valid for Vite (package.json) apps.")
        temp_dist_dir = _materialize_static_dist(source, tier)
        dist_dir = temp_dist_dir

    dist_archive_path: str | None = None
    source_archive_path: str | None = None
    try:
        dist_archive_path = _archive_dist_dir(dist_dir)
        # The Vite tier preserves project source for re-builds; no-build tiers
        # have no separate source, so the dist is the canonical artifact.
        source_root = source if tier == "vite" else dist_dir
        source_archive_path = _archive_source_dir(source_root)
        return pod_sdk.apps.upload_bundle(
            app_name,
            source_archive=source_archive_path,
            dist_archive=dist_archive_path,
        )
    finally:
        for path in (dist_archive_path, source_archive_path):
            if path and os.path.exists(path):
                os.unlink(path)
        if temp_dist_dir is not None and temp_dist_dir.exists():
            shutil.rmtree(temp_dist_dir, ignore_errors=True)


def _safe_archive_member(name: str, target_dir: Path) -> Path | None:
    """Where ``name`` may be written under ``target_dir``, or None to skip it.

    Directory entries are skipped (their parents are created for the files that
    need them). Absolute paths, drive letters, and ``..`` traversal are rejected
    rather than clamped: an archive that tries to escape is a hostile archive,
    and silently rewriting its paths would hide that.
    """
    if not name or name.endswith("/"):
        return None
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"App source archive contains an unsafe path: {name}")
    destination = (target_dir / Path(*candidate.parts)).resolve()
    if not destination.is_relative_to(target_dir.resolve()):
        raise ValueError(f"App source archive contains an unsafe path: {name}")
    return destination


def extract_app_source_archive(
    archive_bytes: bytes,
    target_dir: Path,
    *,
    overwrite: bool = False,
) -> list[str]:
    """Unpack a downloaded app source archive into ``target_dir``.

    Returns the relative paths written, in archive order. Refuses to write into
    a directory that already holds files unless ``overwrite`` is set, so a pull
    never silently clobbers work in progress.
    """
    target_dir = target_dir.expanduser()
    if target_dir.exists() and not target_dir.is_dir():
        raise ValueError(f"App source target is not a directory: {target_dir}")
    if target_dir.is_dir() and any(target_dir.iterdir()) and not overwrite:
        raise ValueError(
            f"{target_dir} is not empty. Re-run with --force to overwrite it."
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_target = target_dir.resolve()
    written: list[str] = []
    with ZipFile(BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            destination = _safe_archive_member(name, resolved_target)
            if destination is None:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))
            written.append(destination.relative_to(resolved_target).as_posix())
    return written
