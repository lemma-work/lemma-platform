from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
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
        raise ValueError(f"App source file must be an .html file, got: {source.name}")
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


def _extract_archive(archive_bytes: bytes, target_dir: Path) -> list[str]:
    """Unpack an app archive into ``target_dir``, refusing to escape it."""
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            destination = target_dir / member.filename
            if not destination.resolve().is_relative_to(target_dir.resolve()):
                raise ValueError(f"Unsafe path in app archive: {member.filename}")
            written.append(member.filename)
        archive.extractall(target_dir)
    return sorted(written)


def pull_app_bundle(
    client: Any,
    *,
    pod_id: str,
    app_name: str,
    target_dir: Path,
    dist: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Download a deployed app into ``target_dir``, ready to edit and redeploy.

    The source archive is what comes back by default: for a Vite app that is the
    project, and for a no-build app it is the single ``index.html``. Either is a
    valid ``lemma apps deploy`` source, so pull → edit → deploy is a closed loop.
    ``dist`` asks for the built output instead, which is what an app deployed
    before its source was stored has.
    """
    if target_dir.exists() and any(target_dir.iterdir()) and not force:
        raise ValueError(
            f"Directory is not empty: {target_dir}. Pass --force to overwrite it."
        )

    pod_sdk = client.pod(pod_id)
    kind = "dist" if dist else "source"
    if dist:
        archive_bytes = pod_sdk.apps.download_dist_archive(app_name)
    else:
        archive_bytes = pod_sdk.apps.download_source_archive(app_name)
        if not archive_bytes:
            # An app deployed before its source was stored has only a dist. Say
            # so rather than writing an empty directory: what lands is built
            # output, which redeploys but is not what the author edits.
            archive_bytes = pod_sdk.apps.download_dist_archive(app_name)
            kind = "dist"

    if not archive_bytes:
        raise ValueError(f"App has no {kind} archive to pull: {app_name}")

    if force and target_dir.exists():
        shutil.rmtree(target_dir)
    files = _extract_archive(archive_bytes, target_dir)
    return {
        "app": app_name,
        "pod_id": pod_id,
        "directory": str(target_dir),
        "archive": kind,
        "files": files,
    }


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
