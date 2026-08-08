"""E2B sandbox provider.

Written against the E2B SDK as it is now, not as the previous adapter found it.
Three capabilities that adapter predated do most of the work here, and each one
removes machinery rather than adding it:

*Metadata queries* give E2B an identity mechanism. A sandbox is created with
`{lemma-sandbox-id, lemma-epoch}` in its metadata and found again by querying
for it. That is the same idea as the deterministic container name on Docker --
identity derived from durable state -- so create is idempotent here too, and
for the same reason: look before creating, and a retry after a lost response
finds the sandbox instead of making a second one.

*Pause and resume* is a real suspend primitive. Releasing a sandbox pauses it
and keeps its disk; the next ensure reconnects to the same sandbox. The
previous adapter had to destroy and recreate, which is why it needed a separate
notion of native storage that outlived the sandbox.

*The sandbox is the disk.* Verified against the real service: write a file,
pause, reconnect, and it is still there. That is how production stores every
workspace today -- the account holds no volumes at all, and volumes are not a
public E2B feature. So `storage_kind` is SANDBOX_NATIVE, and adoption is not
an optimisation but the only safe behaviour: creating a second sandbox for an
existing identity would leave the user's files in the first with nothing
pointing at it. Adoption therefore ignores the epoch, and the fence becomes the
E2B sandbox id, which only changes when the sandbox genuinely is new.

What is deliberately *not* carried over: layered retry loops around every call.
A provider's job is to report what happened; deciding whether to wait and try
again belongs to the service, which is the only layer that knows the caller's
deadline. Errors here are classified and raised, not absorbed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    ProviderCreateSpec,
    ProviderFailed,
    ProviderInstance,
    ProviderObject,
    ProviderRejected,
    ProviderStorageKind,
)
from app.modules.workspace.providers.e2b_common import (
    DEFAULT_METADATA_NAMESPACE,
    LEGACY_LOGICAL_ID,
    LEGACY_MANAGED_BY,
    LEGACY_MANAGED_BY_KEY,
    meta_epoch,
    meta_sandbox_id,
    meta_sandbox_kind,
    sdk_best_effort,
    sdk_errors,
)
from app.modules.workspace.providers.e2b_ops import E2BOpsMixin
from app.modules.workspace.providers.e2b_output import E2BOutputBuffer

WORKSPACE_MOUNT = "/workspace"


@dataclass(frozen=True, slots=True)
class E2BProviderConfig:
    api_key: str
    workspace_template: str
    function_template: str
    # How long E2B keeps a sandbox alive without contact. The service touches
    # activity on use, so this is a backstop against leaking compute when the
    # backend dies, not the primary idle policy.
    sandbox_timeout_seconds: int = 60 * 30
    domain: str | None = None
    # Namespaces every metadata key this provider writes and queries. Changing
    # it makes a provider blind to sandboxes labelled by another namespace,
    # which is exactly what a conformance run against a shared account needs.
    metadata_namespace: str = DEFAULT_METADATA_NAMESPACE
    # Whether pre-consolidation AgentBox sandboxes may be adopted. Off for any
    # namespace but the production one: a test must never adopt a real user's
    # workspace.
    adopt_legacy: bool = True


class E2BSandboxProvider(E2BOpsMixin):
    name = "e2b"
    # A paused E2B sandbox keeps its filesystem, so the sandbox *is* the disk.
    # Verified against the real service: write, pause, reconnect, read back.
    storage_kind = ProviderStorageKind.SANDBOX_NATIVE

    def __init__(
        self, config: E2BProviderConfig, *, output: E2BOutputBuffer | None = None
    ) -> None:
        self._config = config
        self._output = output or E2BOutputBuffer()
        self._watchers: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # SDK access, imported lazily so a Docker-only deployment never loads it
    # ------------------------------------------------------------------

    @property
    def _sdk(self):
        try:
            from e2b import AsyncSandbox
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise ProviderRejected(
                "the e2b extra is not installed; install lemma-backend[e2b]"
            ) from exc
        return AsyncSandbox

    @property
    def _query(self):
        """The metadata-query type, reached through the same seam as the SDK.

        Importing it directly made the provider need the real package even when
        the client itself was faked, so the unit job -- which installs no e2b
        extra, because nothing about a Docker deployment should -- failed on
        every listing test. A substitute for the SDK now substitutes its query
        type with it.
        """

        substitute = getattr(self._sdk, "query_type", None)
        if substitute is not None:
            return substitute
        from e2b.sandbox.sandbox_api import SandboxQuery

        return SandboxQuery

    @property
    def _pty_size(self):
        """The PTY dimensions type, reached through the SDK seam like `_query`."""

        substitute = getattr(self._sdk, "pty_size_type", None)
        if substitute is not None:
            return substitute
        from e2b.sandbox.commands.command_handle import PtySize

        return PtySize

    def _api(self) -> dict[str, object]:
        params: dict[str, object] = {"api_key": self._config.api_key}
        if self._config.domain:
            params["domain"] = self._config.domain
        return params

    def _identity_metadata(self, spec: ProviderCreateSpec) -> dict[str, str]:
        namespace = self._config.metadata_namespace
        return {
            meta_sandbox_id(namespace): str(spec.sandbox_id),
            meta_sandbox_kind(namespace): spec.kind.value,
            meta_epoch(namespace): str(spec.epoch),
        }

    def _template(self, kind: SandboxKind) -> str:
        return (
            self._config.function_template
            if kind is SandboxKind.FUNCTION
            else self._config.workspace_template
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        """Adopt this sandbox's existing E2B sandbox, or make its first one.

        Because the sandbox is also the disk, "create" here means "make sure
        the one sandbox that holds this workspace's files exists and is
        running". Creating a second one would leave the user's files stranded
        in the first, which is the whole reason adoption is not optional --
        production workspaces today are paused sandboxes with no volume behind
        them.

        Adoption deliberately ignores the epoch. On Docker the epoch fences a
        container that can be replaced independently of its volume; here
        replacement would destroy the disk, so identity alone decides, and the
        fence is the E2B sandbox id -- a genuinely new sandbox has a new id, so
        a stale operation fails rather than landing on it.
        """
        existing = await self._find_any(spec.sandbox_id)
        if existing is not None:
            await self._stamp(existing.provider_id, spec)
            return ProviderInstance(
                provider_id=existing.provider_id,
                name=spec.name,
                running=existing.running,
                storage_adopted=True,
            )

        with sdk_errors():
            sandbox = await self._sdk.create(
                template=self._template(spec.kind),
                timeout=self._config.sandbox_timeout_seconds,
                metadata=self._identity_metadata(spec),
                envs=dict(spec.env),
                **self._api(),
            )

        return ProviderInstance(
            provider_id=sandbox.sandbox_id,
            name=spec.name,
            running=True,
            storage_adopted=False,
        )

    async def _stamp(self, provider_id: str, spec: ProviderCreateSpec) -> None:
        """Best effort: record the current epoch on an adopted sandbox.

        Only bookkeeping -- identity and the fence come from the sandbox id, so
        an SDK without metadata updates costs nothing but a stale epoch label.
        """
        with sdk_best_effort():
            sandbox = await self._connect(provider_id)
            setter = getattr(sandbox, "set_metadata", None)
            if setter is not None:
                await setter(self._identity_metadata(spec), **self._api())

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        """Look a sandbox up by the name the service assigned it.

        The service's names carry the identity, so they are parsed back into a
        metadata query rather than being sent to E2B, which knows nothing about
        them.
        """
        from app.modules.workspace.providers import naming

        parsed = naming.parse_container_name(name)
        if parsed is None:
            return None
        sandbox_id, _, _ = parsed
        # Epoch is not part of the lookup: the sandbox is adopted across
        # epochs because destroying it would destroy the user's files.
        return await self._find_any(sandbox_id)

    async def wait_ready(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        """E2B returns a sandbox that is already serving.

        `create` does not resolve until the sandbox accepts commands, and a
        resumed sandbox is reachable as soon as `connect` returns, so there is
        no readiness loop to run. A paused sandbox is resumed here rather than
        being reported not-ready, because resuming is what the caller wants.
        """
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors():
            running = await sandbox.is_running()
        if not running:
            raise ProviderFailed(f"e2b sandbox {instance.provider_id} is not running")

    async def release(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        """Pause, keeping the filesystem. The next ensure resumes this sandbox."""
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors():
            await sandbox.beta_pause(**self._api())

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        instance = await self.inspect(name, deadline_at=deadline_at)
        if instance is None:
            # Already gone is the outcome destroy was asking for.
            return
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors():
            await sandbox.kill(**self._api())

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        """Always None: E2B storage is the sandbox, not a separate volume.

        The service skips this entirely for SANDBOX_NATIVE providers. It is
        implemented so the protocol is satisfied and so a future caller that
        forgets the distinction gets "no volume" rather than a crash.
        """
        return None

    async def ensure_volume(
        self, *, sandbox_id: UUID, name: str, deadline_at: datetime
    ) -> str:
        raise ProviderRejected(
            "e2b storage lives in the sandbox itself; there is no volume to create"
        )

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        """Nothing to destroy separately: killing the sandbox takes the disk."""
        return None

    # ------------------------------------------------------------------
    # Reclamation
    # ------------------------------------------------------------------

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        """Every sandbox carrying this platform's metadata.

        Sandboxes without it belong to something else using the same E2B
        account and are not ours to reap.
        """

        found: list[ProviderObject] = []
        with sdk_errors():
            paginator = self._sdk.list(
                query=self._query(
                    metadata={
                        meta_sandbox_kind(
                            self._config.metadata_namespace
                        ): SandboxKind.WORKSPACE.value
                    }
                ),
                **self._api(),
            )
            pages = await paginator.next_items()

        for info in pages:
            metadata = info.metadata or {}
            raw_id = metadata.get(meta_sandbox_id(self._config.metadata_namespace))
            if not raw_id:
                continue
            try:
                sandbox_id = UUID(raw_id)
            except ValueError:
                continue
            epoch = None
            raw_epoch = metadata.get(meta_epoch(self._config.metadata_namespace))
            if raw_epoch:
                try:
                    epoch = int(raw_epoch)
                except ValueError:
                    epoch = None
            found.append(
                ProviderObject(
                    provider_id=info.sandbox_id,
                    name=info.sandbox_id,
                    sandbox_id=sandbox_id,
                    epoch=epoch,
                    running=str(info.state).lower().endswith("running"),
                    legacy=False,
                )
            )
        return tuple(found)

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _find_any(self, sandbox_id: UUID) -> ProviderInstance | None:
        """The sandbox holding this workspace's files, whatever labelled it.

        Pre-consolidation sandboxes carry ``logical-id`` and
        ``managed-by=agentbox``. Those are live production workspaces, paused
        with the user's files inside them, so failing to match one would not
        merely duplicate compute -- it would hand the user an empty workspace
        and leave their work in a sandbox nothing points at any more.

        Paused sandboxes count as found. Pausing is how storage persists here.
        """
        namespace = self._config.metadata_namespace
        queries: list[dict[str, str]] = [
            {meta_sandbox_id(namespace): str(sandbox_id)}
        ]
        if self._config.adopt_legacy:
            queries.append(
                {
                    LEGACY_MANAGED_BY_KEY: LEGACY_MANAGED_BY,
                    LEGACY_LOGICAL_ID: str(sandbox_id),
                }
            )
        for query in queries:
            match = await self._first_matching(query)
            if match is not None:
                return match
        return None

    async def _first_matching(
        self, metadata: dict[str, str]
    ) -> ProviderInstance | None:
        with sdk_errors():
            paginator = self._sdk.list(
                query=self._query(metadata=metadata), **self._api()
            )
            matches = await paginator.next_items()

        # Prefer a running sandbox when several match, so a duplicate left by
        # an earlier failure does not shadow the one actually serving.
        ordered = sorted(
            matches,
            key=lambda info: str(info.state).lower().endswith("running"),
            reverse=True,
        )
        for info in ordered:
            return ProviderInstance(
                provider_id=info.sandbox_id,
                name=info.sandbox_id,
                volume_name=None,
                running=str(info.state).lower().endswith("running"),
            )
        return None

    async def _connect(self, provider_id: str):
        # Both arms of the old branch raised the same thing; sdk_errors is
        # what that code was spelling out by hand.
        with sdk_errors():
            return await self._sdk.connect(provider_id, **self._api())




