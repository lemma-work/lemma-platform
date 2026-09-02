"""Whether this deployment could provision a sandbox if it were asked.

`build_provider` already raises a specific, actionable error for every
misconfiguration it can see -- a missing `WORKSPACE_RUNTIME_CREDENTIAL_KEY` or
`WORKSPACE_LOCAL_RUNTIME_CLI` or `E2B_API_KEY`, an E2B metadata namespace it
refuses to derive. It is just called lazily, from the sandbox composition, so
the first thing that ever read one of those errors was a user's first tool call,
arriving as `500 INTERNAL_ERROR` with nothing actionable in it. A host with no
Docker at all did not get even that: nothing looks at the socket until an HTTP
request reaches the engine, hours after the install reported itself healthy.

So the API probes once at startup and publishes the answer as a capability. The
exception text goes to the log, where the operator is; the capability carries
the setting to look at and nothing more, because `/health/capabilities` is
unauthenticated.

Lives outside `app/core` deliberately: the probe has to name a concrete fabric,
and `app/core` is what the modules are built on rather than the other way round.
"""

from __future__ import annotations

from pathlib import Path

from app.core.log.log import get_logger

logger = get_logger(__name__)

#: What the last probe found. Same shape and lifetime as the datastore's
#: embedding capability, so `/health/capabilities` answers without rebuilding a
#: provider on every call.
_capability: dict[str, str] = {"status": "unknown", "detail": ""}

_NO_SOCKET = (
    "No Docker Engine socket at WORKSPACE_DOCKER_SOCKET_PATH. Start Docker, or "
    "set WORKSPACE_PROVIDER to a fabric this host has."
)
_UNBUILDABLE = (
    "The configured sandbox provider could not be built. Check WORKSPACE_PROVIDER "
    "and its credentials; the server log names which one is missing."
)


def probe_sandbox_provider() -> dict[str, str]:
    """Build the configured provider once and report what happened."""
    from app.modules.workspace.config import workspace_settings
    from app.modules.workspace.services.provider_factory import build_provider

    provider = workspace_settings.provider
    if (
        provider == "docker"
        and not Path(workspace_settings.docker_socket_path).exists()
    ):
        logger.error(
            "sandbox_health.probe.docker_socket_missing.failed",
            provider=provider,
            socket_path=workspace_settings.docker_socket_path,
        )
        return {"status": "needs_setup", "detail": _NO_SOCKET}
    try:
        build_provider()
    # Narrow on purpose. `build_provider` raises `RuntimeError` for every case
    # it deliberately detects, and the rest is a provider SDK failing to import
    # or a path that will not open. Anything outside that is not a
    # configuration problem this can describe, and it should stop the process
    # at startup rather than be filed under "needs setup".
    except RuntimeError, OSError, ImportError, ValueError:
        logger.error(
            "sandbox_health.probe.provider_unavailable.failed",
            provider=provider,
            exc_info=True,
        )
        return {"status": "needs_setup", "detail": _UNBUILDABLE}
    return {"status": "ready", "detail": f"Sandboxes are provisioned by {provider}"}


def record_sandbox_probe() -> None:
    """Run the probe and keep its answer for `/health/capabilities`."""
    _capability.update(probe_sandbox_provider())


def sandbox_capability() -> dict[str, str]:
    return dict(_capability)
