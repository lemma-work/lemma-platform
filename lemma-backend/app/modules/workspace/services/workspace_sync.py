"""Per-user /workspace persistence: optional sync-credential provider.

The OSS/local build has no cloud workspace persistence. A deployment (e.g.
lemma-cloud) can register a provider that, given a sandbox id, returns the env a
sandbox needs to sync its `/workspace` to a per-user object-storage directory
(a scoped URL + credential). Kept as a tiny registry so the OSS core stays
cloud-agnostic — mirroring the `install_azure_storage_shim` pattern.

The env keys are consumed by the sandbox runtime entrypoint (`start-runtime.sh`):
  WORKSPACE_SYNC_URL  — object-storage directory URL for this user
  WORKSPACE_SYNC_SAS  — scoped credential (SAS/query string) for that directory
"""

from __future__ import annotations

from typing import Callable

from app.core.log.log import get_logger

logger = get_logger(__name__)

WorkspaceSyncProvider = Callable[[str], dict[str, str]]

_provider: WorkspaceSyncProvider | None = None


def register_workspace_sync_provider(provider: WorkspaceSyncProvider) -> None:
    """Register the deployment's sync-credential provider (called once at startup)."""
    global _provider
    _provider = provider


def workspace_sync_env(sandbox_id: str) -> dict[str, str]:
    """Return the sync env for `sandbox_id`, or {} if persistence isn't configured.

    Never raises: a failure to mint sync credentials must not block sandbox
    creation — the sandbox just starts without persistence.
    """
    if _provider is None:
        return {}
    try:
        return _provider(sandbox_id) or {}
    except Exception:
        logger.exception("workspace sync provider failed for sandbox=%s", sandbox_id)
        return {}
