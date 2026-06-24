"""Release manifests: which images make up a stack version.

CI publishes one JSON manifest per release; lemma-stack resolves a channel
(or explicit version / local file) to a manifest, pins it to
~/.lemma/local/release.json, and pulls images by digest when available.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from lemma_stack import __version__
from lemma_stack.output import AdminError, info
from lemma_stack.paths import LocalPaths

SCHEMA_VERSION = 1
DEFAULT_REPO = "lemma-work/lemma-platform"
MANIFEST_ASSET = "lemma-local.json"
APP_IMAGE_KEYS = ("backend", "frontend", "agentbox", "agentbox_runtime")

# Fresh installs get pg16; the dev stack stays on pg15 for volume compat.
DEFAULT_INFRA_IMAGES = {
    "postgres": "docker.io/pgvector/pgvector:0.8.3-pg16",
    "redis": "docker.io/redis/redis-stack:7.2.0-v19",
    "supertokens": "docker.io/supertokens/supertokens-postgresql:11.1.0",
    "kreuzberg": "ghcr.io/kreuzberg-dev/kreuzberg:4.9.9",
}


@dataclass(frozen=True)
class ImageRef:
    ref: str
    digest: str | None = None

    @property
    def pull_ref(self) -> str:
        return f"{self.ref}@{self.digest}" if self.digest else self.ref


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    min_admin_version: str
    images: dict[str, ImageRef]
    infra: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def image(self, key: str) -> ImageRef:
        try:
            return self.images[key]
        except KeyError as exc:
            raise AdminError(f"release manifest is missing image {key!r}") from exc

    def infra_image(self, key: str) -> str:
        return self.infra.get(key) or DEFAULT_INFRA_IMAGES[key]

    def all_pull_refs(self, *, kreuzberg: bool) -> list[str]:
        refs = [self.image(key).pull_ref for key in APP_IMAGE_KEYS]
        refs.extend(
            self.infra_image(key)
            for key in ("postgres", "redis", "supertokens")
        )
        if kreuzberg:
            refs.append(self.infra_image("kreuzberg"))
        return refs


def parse(data: dict[str, Any]) -> ReleaseManifest:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise AdminError(
            f"unsupported manifest schema_version {data.get('schema_version')!r}; "
            "upgrade lemma-stack"
        )
    version = str(data.get("version") or "")
    if not version:
        raise AdminError("release manifest has no version")
    images: dict[str, ImageRef] = {}
    for key, value in (data.get("images") or {}).items():
        if isinstance(value, str):
            images[key] = ImageRef(ref=value)
        elif isinstance(value, dict) and value.get("ref"):
            images[key] = ImageRef(ref=str(value["ref"]), digest=value.get("digest"))
        else:
            raise AdminError(f"invalid image entry for {key!r} in release manifest")
    missing = [key for key in APP_IMAGE_KEYS if key not in images]
    if missing:
        raise AdminError(f"release manifest is missing images: {', '.join(missing)}")
    manifest = ReleaseManifest(
        version=version,
        min_admin_version=str(data.get("min_admin_version") or "0"),
        images=images,
        infra={str(k): str(v) for k, v in (data.get("infra") or {}).items()},
        raw=data,
    )
    check_admin_version(manifest)
    return manifest


def check_admin_version(manifest: ReleaseManifest) -> None:
    try:
        required = Version(manifest.min_admin_version)
        current = Version(__version__)
    except InvalidVersion:
        return
    if current < required:
        raise AdminError(
            f"release {manifest.version} requires lemma-stack >= {manifest.min_admin_version} "
            f"(you have {__version__}); run: uv tool upgrade lemma-stack"
        )


def release_url(channel: str) -> str:
    """stable -> the latest GitHub release's manifest asset; otherwise a version tag."""
    explicit = os.environ.get("LEMMA_STACK_RELEASE_URL")
    if explicit:
        return explicit
    base = os.environ.get(
        "LEMMA_STACK_RELEASE_BASE_URL",
        f"https://github.com/{DEFAULT_REPO}/releases",
    ).rstrip("/")
    if channel == "stable":
        return f"{base}/latest/download/{MANIFEST_ASSET}"
    return f"{base}/download/v{channel.lstrip('v')}/{MANIFEST_ASSET}"


def fetch(channel: str, *, github_auth: bool | None = None) -> ReleaseManifest:
    """Fetch a manifest; github_auth True forces the authenticated API path,
    None auto-falls-back to it when the public URL fails (private repo)."""
    from lemma_stack.release import github

    if github_auth:
        token = github.github_token()
        if not token:
            raise AdminError(
                "--github-auth requested but no token found; "
                "run `gh auth login` or set GITHUB_TOKEN"
            )
        return parse(github.fetch_manifest_via_api(DEFAULT_REPO, channel, MANIFEST_ASSET, token))

    url = release_url(channel)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        if github_auth is None and "github.com" in url:
            token = github.github_token()
            if token:
                info("public manifest fetch failed; retrying via the GitHub API (private repo?)")
                return parse(
                    github.fetch_manifest_via_api(DEFAULT_REPO, channel, MANIFEST_ASSET, token)
                )
        raise AdminError(f"could not fetch release manifest from {url}: {exc}") from exc
    return parse(data)


def load_file(path: Path) -> ReleaseManifest:
    try:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdminError(f"could not read release manifest {path}: {exc}") from exc


def pin(paths: LocalPaths, manifest: ReleaseManifest) -> None:
    """Record the installed release; archive the previous one for rollback."""
    if paths.release_file.exists():
        try:
            previous = json.loads(paths.release_file.read_text(encoding="utf-8"))
            prev_version = previous.get("version", "unknown")
            paths.releases_dir.mkdir(parents=True, exist_ok=True)
            (paths.releases_dir / f"lemma-{prev_version}.json").write_text(
                json.dumps(previous, indent=2), encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError):
            pass
    paths.release_file.write_text(json.dumps(manifest.raw, indent=2), encoding="utf-8")


def load_pinned(paths: LocalPaths) -> ReleaseManifest:
    if not paths.release_file.exists():
        raise AdminError("no installed release found; run `lemma-stack install` first")
    return load_file(paths.release_file)
