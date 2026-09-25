"""The ``lemma-agent-host`` binary the e2e tests drive, refused when stale.

These tests run the real binary from ``desktop/target/debug``, which nothing
rebuilds between a source change and a test run. A stale build fails as
whatever the newer backend expects of it -- once as ``SandboxProcessNotFound``
from an exec-server that still named processes the old way -- which names
neither the cause nor the fix. So the default build is compared with the
sources it comes from, and an older one is refused by name.

``LEMMA_AGENT_HOST_E2E_BINARY`` is taken as given: whoever points at a binary
chose it.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parents[5]
_DEFAULT = _REPOSITORY / "desktop/target/debug/lemma-agent-host"
_SOURCES = (
    _REPOSITORY / "desktop/agent-host/src",
    _REPOSITORY / "desktop/agent-host/resources",
    _REPOSITORY / "desktop/agent-host/Cargo.toml",
    _REPOSITORY / "desktop/Cargo.lock",
)
_REBUILD = "Build it with: make desktop-agent-host-e2e (or cargo build -p lemma-agent-host in desktop/)"


def _newest_source() -> tuple[float, Path | None]:
    newest: tuple[float, Path | None] = (0.0, None)
    for source in _SOURCES:
        files = source.rglob("*") if source.is_dir() else [source]
        for path in files:
            if path.is_file() and (mtime := path.stat().st_mtime) > newest[0]:
                newest = (mtime, path)
    return newest


def agent_host_binary() -> Path:
    """The binary to run: the override, or a debug build no older than its sources."""
    override = os.environ.get("LEMMA_AGENT_HOST_E2E_BINARY")
    binary = Path(override) if override else _DEFAULT
    assert binary.is_file(), f"lemma-agent-host is not built at {binary}. {_REBUILD}"
    if override:
        return binary
    newest, source = _newest_source()
    assert binary.stat().st_mtime >= newest, (
        f"lemma-agent-host at {binary} is older than {source}, so these tests would "
        f"exercise yesterday's host against today's backend. {_REBUILD}"
    )
    return binary
