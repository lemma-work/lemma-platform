"""Release manifests: which images make up a stack version.

CI publishes one JSON manifest per release; lemma-stack resolves a channel
(or explicit version / local file) to a manifest, pins it to
~/.lemma/local/release.json, and pulls images by digest when available.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from lemma_stack import __version__
from lemma_stack.output import AdminError
from lemma_stack.paths import LocalPaths

# Host packs are an additive field in schema 1. Keeping the public schema
# stable lets already-installed lemma-stack clients continue consuming new
# releases while new Desktop builds opt into and verify the native artifact.
SCHEMA_VERSION = 1
DEFAULT_REPO = "lemma-work/lemma-platform"
MANIFEST_ASSET = "lemma-local.json"
APP_IMAGE_KEYS = ("backend", "frontend", "agentbox_runtime")

# Fresh installs get pg16; the dev stack stays on pg15 for volume compat.
DEFAULT_INFRA_IMAGES = {
    "postgres": "docker.io/pgvector/pgvector:0.8.3-pg16",
    "redis": "docker.io/redis/redis-stack:7.2.0-v19",
    "supertokens": "docker.io/supertokens/supertokens-postgresql:11.4.5",
}


@dataclass(frozen=True)
class ImageRef:
    ref: str
    digest: str | None = None

    @property
    def pull_ref(self) -> str:
        return f"{self.ref}@{self.digest}" if self.digest else self.ref


@dataclass(frozen=True)
class ArtifactRef:
    url: str
    sha256: str
    size: int
    format: str = "zip"


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    min_admin_version: str
    images: dict[str, ImageRef]
    infra: dict[str, str] = field(default_factory=dict)
    host_packs: dict[str, ArtifactRef] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def image(self, key: str) -> ImageRef:
        try:
            return self.images[key]
        except KeyError as exc:
            raise AdminError(f"release manifest is missing image {key!r}") from exc

    def infra_image(self, key: str) -> str:
        return self.infra.get(key) or DEFAULT_INFRA_IMAGES[key]

    def all_pull_refs(self) -> list[str]:
        refs = [self.image(key).pull_ref for key in APP_IMAGE_KEYS]
        refs.extend(self.infra_pull_refs())
        return refs

    def infra_pull_refs(self) -> list[str]:
        """Images needed before native backend/frontend processes can start.

        The sandbox runtime is intentionally absent: the selected AgentBox
        provider fetches it on first sandbox creation instead of making every
        desktop install pay that download cost.
        """

        return [
            self.infra_image(key)
            for key in ("postgres", "redis", "supertokens")
        ]

    def host_pack(self, target: str) -> ArtifactRef:
        try:
            return self.host_packs[target]
        except KeyError as exc:
            raise AdminError(
                f"release {self.version} has no native host pack for {target}"
            ) from exc


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
    host_packs: dict[str, ArtifactRef] = {}
    for target, value in (data.get("host_packs") or {}).items():
        if not isinstance(value, dict):
            raise AdminError(f"invalid host pack entry for {target!r}")
        url = str(value.get("url") or "")
        sha256 = str(value.get("sha256") or "")
        size = value.get("size")
        archive_format = str(value.get("format") or "zip")
        if (
            not url.startswith("https://")
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or not isinstance(size, int)
            or size <= 0
            or archive_format != "zip"
        ):
            raise AdminError(f"invalid host pack entry for {target!r}")
        host_packs[str(target)] = ArtifactRef(
            url=url,
            sha256=sha256,
            size=size,
            format=archive_format,
        )
    manifest = ReleaseManifest(
        version=version,
        min_admin_version=str(data.get("min_admin_version") or "0"),
        images=images,
        infra={str(k): str(v) for k, v in (data.get("infra") or {}).items()},
        host_packs=host_packs,
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


def fetch(channel: str) -> ReleaseManifest:
    """Download the public release manifest for a channel (stable) or version."""
    url = release_url(channel)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
            where = "the stable channel" if channel == "stable" else f"version {channel}"
            raise AdminError(
                f"no release manifest published for {where} yet "
                f"({MANIFEST_ASSET} not found at {url}).\n"
                "A release may not be cut yet (or is still building). Options:\n"
                "  • install a specific version:  lemma-stack install --channel <X.Y.Z>\n"
                "  • install from a local file:   lemma-stack install --manifest <path>"
            ) from exc
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
