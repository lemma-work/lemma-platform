"""Install the native Agent Host binary used by ``lemma agent-host``."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lemma_cli import __version__

_MAX_BINARY_BYTES = 128 * 1024 * 1024
_MAX_CHECKSUM_BYTES = 4096
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class _MissingRelease(RuntimeError):
    """A release asset this CLI version expects is not published."""


def _target_triple() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x64"}:
        machine = "x86_64"
    elif machine in {"arm64", "armv8"}:
        machine = "aarch64"

    if sys.platform == "darwin" and machine in {"aarch64", "x86_64"}:
        return f"{machine}-apple-darwin"
    if sys.platform.startswith("linux") and machine in {"aarch64", "x86_64"}:
        return f"{machine}-unknown-linux-gnu"
    if sys.platform == "win32" and machine == "x86_64":
        return "x86_64-pc-windows-msvc"
    raise RuntimeError(
        "Agent Host is not released for this platform yet "
        f"({sys.platform}/{platform.machine()}). Set LEMMA_AGENT_HOST_BIN to "
        "a compatible binary."
    )


def _binary_name() -> str:
    return "lemma-agent-host.exe" if sys.platform == "win32" else "lemma-agent-host"


def managed_binary_path() -> Path:
    configured = os.getenv("LEMMA_AGENT_HOST_INSTALL_DIR")
    if configured:
        root = Path(configured).expanduser()
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support/Lemma/agent-host"
    elif sys.platform == "win32":
        local_app_data = os.getenv("LOCALAPPDATA")
        root = (
            Path(local_app_data).expanduser() / "Lemma/agent-host"
            if local_app_data
            else Path.home() / "AppData/Local/Lemma/agent-host"
        )
    else:
        data_home = os.getenv("XDG_DATA_HOME")
        root = (
            Path(data_home).expanduser() / "lemma/agent-host"
            if data_home
            else Path.home() / ".local/share/lemma/agent-host"
        )
    return root / __version__ / _binary_name()


def _release_base_url() -> str:
    configured = os.getenv("LEMMA_AGENT_HOST_RELEASE_BASE_URL")
    if configured:
        return configured.rstrip("/")
    version = urllib.parse.quote(__version__, safe=".-")
    return (
        "https://github.com/lemma-work/lemma-platform/releases/download/"
        f"v{version}"
    )


def _download(url: str, *, limit: int) -> bytes:
    request = urllib.request.Request(  # noqa: S310 - URL is pinned or explicit.
        url,
        headers={"User-Agent": f"lemma-terminal/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > limit:
                raise RuntimeError(f"download exceeds the {limit}-byte limit")
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        # 404 means the release or this platform's asset was never published, which
        # needs different advice from a transport failure. HTTPError is an OSError,
        # so this must stay ahead of the generic handler.
        if exc.code == 404:
            raise _MissingRelease(url) from exc
        raise RuntimeError(f"could not download {url}: {exc}") from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError(f"could not download {url}: {exc}") from exc
    if len(data) > limit:
        raise RuntimeError(f"download exceeds the {limit}-byte limit")
    return data


def _unavailable_message(*, target: str, asset_name: str, base_url: str) -> str:
    """Explain a 404 in terms of what the user can actually do next."""
    return (
        f"Lemma {__version__} has no Agent Host build for {target}.\n"
        f"Looked for {asset_name} under {base_url}\n"
        "\n"
        "Fix it with any of:\n"
        "  * Upgrade the CLI, which points at a newer release:\n"
        "      uv tool upgrade lemma-terminal\n"
        "  * Point at a binary you already have:\n"
        "      export LEMMA_AGENT_HOST_BIN=/path/to/lemma-agent-host\n"
        "  * Build it from the repository checkout:\n"
        "      cargo build --release --manifest-path agent-host/Cargo.toml\n"
        "  * Point at a different download location:\n"
        "      export LEMMA_AGENT_HOST_RELEASE_BASE_URL=https://.../download/vX.Y.Z\n"
        "\n"
        "If this platform should be supported, report it at\n"
        "https://github.com/lemma-work/lemma-platform/issues"
    )


def install_agent_host(*, force: bool = False) -> Path:
    """Install this CLI release's Agent Host atomically and return its path."""
    destination = managed_binary_path()
    if destination.is_file() and not force:
        return destination

    target = _target_triple()
    extension = ".exe" if sys.platform == "win32" else ""
    asset_name = f"lemma-agent-host-{target}{extension}"
    base_url = _release_base_url()
    asset_url = f"{base_url}/{urllib.parse.quote(asset_name)}"
    checksum_url = f"{asset_url}.sha256"

    unavailable = _unavailable_message(
        target=target,
        asset_name=asset_name,
        base_url=base_url,
    )
    try:
        checksum_text = _download(
            checksum_url,
            limit=_MAX_CHECKSUM_BYTES,
        ).decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"release checksum is invalid: {checksum_url}") from exc
    except _MissingRelease as exc:
        raise RuntimeError(unavailable) from exc
    checksum_fields = checksum_text.split(maxsplit=1)
    expected_checksum = checksum_fields[0] if checksum_fields else ""
    if not _SHA256_RE.fullmatch(expected_checksum):
        raise RuntimeError(f"release checksum is invalid: {checksum_url}")

    try:
        binary = _download(asset_url, limit=_MAX_BINARY_BYTES)
    except _MissingRelease as exc:
        raise RuntimeError(unavailable) from exc
    actual_checksum = hashlib.sha256(binary).hexdigest()
    if actual_checksum.lower() != expected_checksum.lower():
        raise RuntimeError(
            f"Agent Host checksum mismatch for {asset_name}: the release records "
            f"{expected_checksum.lower()} but the download hashed to "
            f"{actual_checksum}. Nothing was installed. Retry in case the download "
            "was truncated; if it keeps failing, do not run the binary and report "
            "it at https://github.com/lemma-work/lemma-platform/issues"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{_binary_name()}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(binary)
            stream.flush()
            os.fsync(stream.fileno())
        if sys.platform != "win32":
            temporary.chmod(
                stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IXUSR
                | stat.S_IRGRP
                | stat.S_IXGRP
                | stat.S_IROTH
                | stat.S_IXOTH
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


__all__ = ["install_agent_host", "managed_binary_path"]
