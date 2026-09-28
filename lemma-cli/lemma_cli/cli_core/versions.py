"""Version + install introspection for `lemma --version` and `lemma doctor`.

Kept deliberately light (stdlib + lazy SDK imports) so the `--version` fast path
does not pull in the HTTP stack or the command modules.
"""

from __future__ import annotations

import importlib.metadata as _md
import os
from collections.abc import MutableMapping
from pathlib import Path


def cli_version() -> str:
    try:
        from lemma_cli import __version__

        return __version__
    except Exception:
        return "unknown"


def declare_client(environ: MutableMapping[str, str]) -> None:
    """Say what we are on every request, through the variables the SDK reads.

    The SDK cannot tell a CLI invocation apart from any other program using it,
    and "a person ran a command" is a different fact from "something called the
    API". The version is the CLI's own, which is what the server compares with
    its release to suggest an upgrade; a caller that named itself something else
    keeps whatever version it declared.
    """
    if environ.setdefault("LEMMA_CLIENT", "lemma-cli") != "lemma-cli":
        return
    environ["LEMMA_CLIENT_VERSION"] = cli_version()


def sdk_dist_version() -> str:
    """Installed lemma-sdk package version (the thing that was stale at 0.2.0)."""
    try:
        return _md.version("lemma-sdk")
    except Exception:
        return "unknown"


def bundled_api_version() -> str | None:
    """OpenAPI ``info.version`` the installed SDK was generated against."""
    try:
        from lemma_sdk import _spec_info

        return _spec_info.API_VERSION
    except Exception:
        return None


def bundled_spec_sha() -> str | None:
    try:
        from lemma_sdk import _spec_info

        return _spec_info.SPEC_SHA256
    except Exception:
        return None


def lemma_executables() -> list[str]:
    """Distinct resolved paths of a `lemma` executable on PATH.

    Deduplicated by realpath so duplicate PATH entries (the common case) do not
    look like multiple installs; >1 entry here means genuinely separate installs.
    """
    seen_real: set[str] = set()
    ordered: list[str] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        for name in ("lemma", "lemma.exe"):
            candidate = Path(directory) / name
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    real = os.path.realpath(candidate)
                    if real not in seen_real:
                        seen_real.add(real)
                        ordered.append(real)
            except OSError:
                continue
    return ordered


def local_version_payload() -> dict[str, object]:
    sha = bundled_spec_sha()
    return {
        "lemma_cli": cli_version(),
        "lemma_sdk": sdk_dist_version(),
        "api_schema": bundled_api_version() or "unknown",
        "spec_sha256": sha[:12] if sha else None,
    }


def version_lines() -> list[str]:
    """Plain lines for the eager `--version` flag (no network, no state)."""
    sha = bundled_spec_sha()
    schema = bundled_api_version() or "unknown"
    suffix = f", spec {sha[:12]}" if sha else ""
    return [
        f"lemma {cli_version()}",
        f"lemma-sdk {sdk_dist_version()} (api schema {schema}{suffix})",
    ]
