"""The runtime bundle this backend installs into workspace sandboxes.

Built by ``scripts/build_runtime_bundle.py`` and shipped *inside the backend
image*, deliberately. The alternative -- publishing it somewhere and pointing a
setting at it -- reintroduces the exact failure this whole change exists to
remove: a second artifact to promote, promoted separately, so that "the backend
and the sandboxes agree" becomes a thing someone has to remember rather than a
property of the deploy. Here a rollback of the backend is a rollback of the
bundle, because they are one artifact.

Loading is pure I/O: a directory is read, or there is no bundle. An earlier
version built from the working tree when the monorepo sources were present,
which read well and was wrong -- it meant a unit test that touched `get_session`
shelled out to `uv build`, so the behaviour of the system depended on whether
the machine running it happened to be a developer's checkout. Development points
`WORKSPACE_RUNTIME_BUNDLE_DIR` at a bundle built by `make runtime-bundle`
instead, which is explicit and costs nothing anywhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings

logger = get_logger(__name__)

#: Where the image puts it. Not a setting: an operator pointing this somewhere
#: else is an operator decoupling the bundle from the backend that installs it.
IMAGE_BUNDLE_DIR = Path("/app/runtime-bundle")


@dataclass(frozen=True, slots=True)
class RuntimeBundle:
    """One immutable payload, and the digest that names it."""

    version: str
    archive: bytes
    archive_sha256: str
    requires: tuple[str, ...]
    #: The release the first-party projects agree on, e.g. "0.8.0". Carried for
    #: people rather than for the code: `version` is the identity and is
    #: stronger, but a log line saying which release a sandbox is running should
    #: not require resolving a hash to read.
    component_version: str = "unknown"

    @property
    def size_bytes(self) -> int:
        return len(self.archive)


def _required_text(manifest: dict[str, object], key: str) -> str:
    """One manifest field that has to be a non-empty string, or it is not one.

    `str(value)` would accept anything -- a dict becomes `"{'a': 1}"` and a
    version that reads like a Python repr is worse than no bundle, because it
    is recorded as the version a sandbox is running.
    """
    value = manifest[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"manifest {key!r} must be a non-empty string")
    return value


def _required_modules(manifest: dict[str, object]) -> tuple[str, ...]:
    """The modules the smoke test imports, which must be a list of names.

    The lenient form of this (`tuple(str(i) for i in value)`) iterates whatever
    it is handed: a bare string becomes one "module" per character, a mapping
    becomes its keys. Neither is rejected, and that is the trap -- the bundle
    loads, `runtime_bundle()` prefers a configured directory over the image's,
    and every ensure then fails its smoke test and retries, forever, while a
    perfectly good bundle sits in the image unused.
    """
    value = manifest.get("requires", ())
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("manifest 'requires' must be a list of module names")
    modules = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError("manifest 'requires' must hold non-empty strings")
        modules.append(item)
    return tuple(modules)


def load_bundle(directory: Path) -> RuntimeBundle | None:
    """Read one bundle directory, or None when there is not one there.

    Separate from ``runtime_bundle`` and public because it is the part with
    behaviour: which directory to look in is configuration, while what a
    directory has to contain to count is a contract worth testing directly
    rather than through a patched settings singleton.
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    # Anything malformed here is "there is no usable bundle at this path", not
    # an exception. Raising would take the *caller's* fallback with it: a
    # half-written directory named by a setting would stop the image's own good
    # bundle from ever being tried, and a deployment would lose the mechanism
    # over a truncated file.
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a JSON object")
        archive = directory / _required_text(manifest, "archive")
        payload = archive.read_bytes()
        bundle = RuntimeBundle(
            version=_required_text(manifest, "version"),
            archive=payload,
            archive_sha256=_required_text(manifest, "archive_sha256"),
            requires=_required_modules(manifest),
            component_version=str(manifest.get("component_version", "unknown")),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "workspace.runtime_bundle.unusable.degraded",
            directory=str(directory),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None
    return bundle


@lru_cache(maxsize=1)
def runtime_bundle() -> RuntimeBundle | None:
    """The bundle this process installs, or None when there is none to install.

    An object singleton read once from disk, which is why this is an
    ``lru_cache`` rather than Redis: it is not data, and every process needs the
    same bytes in memory to hand to the provider.

    Returning None rather than raising is the point. A Docker or `lemma_local`
    deployment that has not adopted the overlay still has a working sandbox --
    the image's own copy of the first-party code -- so the absence of a bundle
    must degrade to "nothing to install", not to a backend that will not serve.
    """
    configured = workspace_settings.runtime_bundle_dir
    for candidate in (Path(configured) if configured else None, IMAGE_BUNDLE_DIR):
        if candidate is None:
            continue
        bundle = load_bundle(candidate)
        if bundle is not None:
            logger.info(
                "workspace.runtime_bundle.loaded",
                version=bundle.version,
                component_version=bundle.component_version,
                size_bytes=bundle.size_bytes,
                source=str(candidate),
            )
            return bundle

    logger.info("workspace.runtime_bundle.absent")
    return None


__all__ = ["IMAGE_BUNDLE_DIR", "RuntimeBundle", "load_bundle", "runtime_bundle"]
