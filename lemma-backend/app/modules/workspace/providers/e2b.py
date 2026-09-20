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
workspace -- the account holds no volumes. E2B does have volumes now, and they
are in the pinned SDK, but they were considered and rejected: they are network
storage, which is what pod files already are, so mounting one here would add a
second remote filesystem to hold what this one already holds. So `storage_kind`
is SANDBOX_NATIVE, and adoption is not an optimisation but the only safe
behaviour: creating a second sandbox for an existing identity would leave the
user's files in the first with nothing pointing at it. Adoption therefore
ignores the epoch, and the fence becomes the E2B sandbox id, which only changes
when the sandbox genuinely is new.

Because the sandbox is the disk, *nothing here replaces a workspace*. Drift is
recorded and adopted; see `create`.

What is deliberately *not* carried over: layered retry loops around every call.
A provider's job is to report what happened; deciding whether to wait and try
again belongs to the service, which is the only layer that knows the caller's
deadline. Errors here are classified and raised, not absorbed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID


from app.core.log.log import get_logger
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    ProviderCreateSpec,
    ProviderGone,
    ProviderInstance,
    ProviderObject,
    ProviderRejected,
    ProviderStorageKind,
)
from app.modules.workspace.providers.e2b_connections import SandboxConnections
from app.modules.workspace.providers.e2b_common import (
    budget_until,
    close_browser,
    ensure_serving,
    meta_epoch,
    meta_profile_digest,
    meta_sandbox_id,
    meta_sandbox_kind,
    meta_template,
    sdk_errors,
    every_page as _every_page,
)
from app.modules.workspace.providers.profiles import profile_for
from app.modules.workspace.providers.e2b_config import (
    CLOSED_TO_THE_INTERNET,
    E2BProviderConfig,
    lifecycle_for,
)
from app.modules.workspace.providers.e2b_ops import E2BOpsMixin
from app.modules.workspace.providers.e2b_output import E2BOutputBuffer

logger = get_logger(__name__)


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
        #: One live connection per sandbox. See `e2b_connections`.
        self._connections = SandboxConnections()

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
            meta_profile_digest(namespace): spec.profile_digest,
            meta_template(namespace): self._template(spec.kind),
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

        It does not ignore what the sandbox was built from, but what it *does*
        about it depends entirely on whether the sandbox owns a disk. See
        `_drift` and the branch below.
        """
        existing = await self._find_any(spec.sandbox_id)
        drift = () if existing is None else self._drift(existing, spec)
        if drift and spec.kind is SandboxKind.FUNCTION:
            # A function sandbox owns no durable disk -- it refetches an
            # immutable artifact from the gateway -- so replacing it to adopt
            # what is configured now costs a cold start and nothing else. It is
            # also the kind where staleness has already caused a P0: a runtime
            # the backend could no longer talk to answered 502 for 100 minutes.
            await self._kill_quietly(existing.provider_id)
            existing = None
        elif drift:
            # A workspace is adopted instead, and this asymmetry is the entire
            # policy: here the sandbox *is* the disk, so replacing it to adopt a
            # newer artifact deletes the user's files.
            #
            # This branch used to fire on the template and kill. The reasoning
            # was sound -- a workspace that is never replaced is a workspace
            # that can never be fixed, and the fleet was pinned to whatever
            # template each sandbox was first created on, so every fix shipped
            # in an image reached nobody who already had a workspace. But the
            # remedy cost the disk, which made publishing a template and
            # destroying the fleet the same act, and the first-party code that
            # forced most of those publications is now delivered into a running
            # sandbox instead (`services/workspace_runtime_bundle`). Repairing
            # a workspace no longer requires replacing it, so this no longer
            # does.
            #
            # `Sandbox fabric` README §6 is what this restores: "While a
            # workspace owns a disk it keeps running the profile it was created
            # with, no generation fence is raised, and nothing is replaced."
            # The accepted cost, stated there too, is that a workspace may run
            # an N-1 *base image* until it is naturally recreated. Closing that
            # gap without destroying anything is a separate mechanism -- copy
            # the disk to a sandbox on the new template -- not a kill here.
            logger.info(
                "workspace.e2b.drift_tolerated",
                sandbox_id=str(spec.sandbox_id),
                drifted=",".join(drift),
                recorded=existing.template or "<unstamped>",
                configured=self._template(spec.kind),
            )
        if existing is not None:
            # Nothing is re-stamped onto the adopted sandbox. The metadata E2B
            # holds is written once, at create, and is immutable thereafter --
            # the SDK has no way to update it. So every reader must treat those
            # values as "what this sandbox was created as", never as "what its
            # row says now", and the epoch in particular is not a fence here.
            #
            # `template` is carried up rather than dropped: now that drift is
            # tolerated it is the only honest answer to "what is this workspace
            # actually running", and that question has to stay answerable.
            return ProviderInstance(
                provider_id=existing.provider_id,
                name=spec.name,
                running=existing.running,
                storage_adopted=True,
                profile_digest=spec.profile_digest,
                template=existing.template,
            )

        with sdk_errors():
            sandbox = await self._sdk.create(
                template=self._template(spec.kind),
                timeout=self._config.sandbox_timeout_seconds,
                lifecycle=lifecycle_for(spec.kind),
                metadata=self._identity_metadata(spec),
                envs=dict(spec.env),
                network=CLOSED_TO_THE_INTERNET,
                **self._api(),
            )

        return ProviderInstance(
            provider_id=sandbox.sandbox_id,
            name=spec.name,
            running=True,
            storage_adopted=False,
        )

    def _drift(
        self, existing: ProviderInstance, spec: ProviderCreateSpec
    ) -> tuple[str, ...]:
        """What this sandbox was built from that is no longer configured.

        Two facts, reported separately because they are not equally trustworthy.
        `template` is the identity of the artifact actually running, and an
        unstamped sandbox counts as drifted: it predates the stamp, so it is at
        least one template behind by construction, and reading "unknown" as
        "fine" is how the fleet's staleness became invisible in the first place.
        `profile_digest` is a hand-maintained environment variable, so it says
        rather less -- which is why the caller must never let it decide anything
        a mistyped deploy could not survive.

        Returning which ones drifted rather than a bare bool so the log can say.
        Once the answer stopped being "replace it", the answer became "record
        it", and a record that cannot tell a republished base image from a
        fat-fingered env var is not worth keeping.
        """
        drifted = []
        if existing.template != self._template(spec.kind):
            drifted.append("template")
        if existing.profile_digest != spec.profile_digest:
            drifted.append("profile_digest")
        return tuple(drifted)

    async def _kill_quietly(self, provider_id: str) -> None:
        """Kill a sandbox we have decided to replace.

        Not best-effort: if the stale sandbox survives, the fresh one carries
        the same sandbox-id metadata and a later lookup could land on either.
        Already gone is the outcome this wanted, though, and it is a normal
        race -- the listing that found it can be stale, or E2B can reap it
        first. `ProviderGone` is a bare RuntimeError that `_provision` does not
        catch, so letting it escape would leave the instance row stuck in
        CREATING until the claim times out.
        """
        try:
            sandbox = await self._connect(provider_id)
            with sdk_errors():
                await sandbox.kill(**self._api())
        except ProviderGone:
            pass

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
        """Ready means the thing this kind's operations depend on answers.

        `is_running()` alone passed a sandbox whose runtime had died, because a
        VM outlives its process. See `ensure_serving`.
        """
        sandbox = await self._connect(instance.provider_id)
        await ensure_serving(
            sandbox,
            instance.provider_id,
            kind=kind,
            runtime_port=profile_for(kind).runtime_port,
            budget_seconds=budget_until(deadline_at),
        )

    async def release(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        """Pause, keeping the filesystem. The next ensure resumes this sandbox.

        `keep_memory=False` because the SDK's default is `True`, and this call
        passed nothing -- so every workspace pause was preserving resident
        memory, while both architecture documents said the opposite:
        "A workspace pause is filesystem-only. Files persist; running processes
        and interpreter state do not, and callers must not treat them as
        recoverable." Auto-resume is already disabled here, which E2B only
        requires *because* of filesystem-only snapshots, so the rest of the
        design had been built around a property the one call that decides it
        never asked for.

        That gap is what made a leaked browser invisible. A memory-preserving
        pause snapshots whatever is running and hands it to the next
        conversation, so a headed Chrome survived every idle release without
        ever being started again -- 63 processes and 2123 MB on a 2048 MB
        sandbox, restored rather than respawned. Reading the docs told you it
        could not happen.

        A filesystem-only pause is also why the browser is closed first. It
        freezes a running Chrome the way pulling the power would, and a frozen
        Chrome has written nothing back: `agent-browser` runs it on a
        throwaway profile under `/tmp` and copies that to the durable one only
        when it is closed cleanly. So somebody who signed in to a site and had
        their sandbox released a moment later lost the login. Measured on a
        real E2B sandbox: sign in, pause immediately, resume, and the cookie is
        gone; close the browser first and it is there. Docker gets this from
        quiesce, which the E2B path has no equivalent of.
        """
        # Functions keep memory -- same rule as `_lifecycle` uses for timeouts.
        keep_memory = kind is SandboxKind.FUNCTION
        sandbox = await self._connect(instance.provider_id)
        if kind is SandboxKind.WORKSPACE:
            await close_browser(sandbox, instance.provider_id, **self._api())
        try:
            with sdk_errors():
                await sandbox.pause(keep_memory=keep_memory, **self._api())
        finally:
            # A held connection to a sandbox this process has just paused is a
            # connection to something that is no longer running, and connecting
            # is what resumes it. Keeping it would make the next operation act
            # as though the sandbox were up and fail -- which is exactly what
            # `test_a_paused_sandbox_is_found_and_resumed` caught when the hold
            # was first added.
            self._forget(instance.provider_id)

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        """Kill a sandbox, addressed either by container name or by E2B id.

        Both, because both are handed to this. `inspect` only understands
        container names -- it parses one back into a metadata query -- but
        `list_objects` names every object by its E2B sandbox id, since that is
        what E2B reports. So the sweep's `destroy(obj.name)` parsed to nothing,
        read as "already gone", and returned having killed nothing. That is why
        the orphan sweep logged eighteen reclaims every five minutes for hours
        against the same eighteen ids while the account's count never moved:
        the destroy was a no-op for exactly the objects the sweep discovers.
        """
        from app.modules.workspace.providers import naming

        instance = await self.inspect(name, deadline_at=deadline_at)
        provider_id = instance.provider_id if instance is not None else None
        if provider_id is None and naming.parse_container_name(name) is None:
            # Not one of our names, so it is an id -- which is the only other
            # thing this is ever given, and `list_objects` has already
            # established that it carries this platform's metadata.
            provider_id = name
        if provider_id is None:
            # A name that parses but resolves to nothing really is gone, and
            # that is the outcome destroy was asking for.
            return
        sandbox = await self._connect(provider_id)
        try:
            with sdk_errors():
                await sandbox.kill(**self._api())
        finally:
            # Whether or not the kill succeeded, this process must stop handing
            # out a connection to it. In the failure case the next caller
            # re-connects and finds out honestly.
            self._forget(provider_id)

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
        return

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
        # Every kind, because the docstring above says "every sandbox carrying
        # this platform's metadata" and this queried only workspaces -- so a
        # function sandbox the control plane had forgotten was invisible to the
        # sweep and billed forever, which is the one thing orphan reclamation
        # exists to stop.
        namespace = self._config.metadata_namespace
        pages: list = []
        for kind in SandboxKind:
            with sdk_errors():
                pages.extend(
                    await _every_page(
                        self._sdk.list(
                            query=self._query(
                                metadata={meta_sandbox_kind(namespace): kind.value}
                            ),
                            **self._api(),
                        )
                    )
                )

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
        """The sandbox holding this workspace's files.

        Paused sandboxes count as found. Pausing is how storage persists here.
        """
        namespace = self._config.metadata_namespace
        queries: list[dict[str, str]] = [{meta_sandbox_id(namespace): str(sandbox_id)}]
        for query in queries:
            match = await self._first_matching(query)
            if match is not None:
                return match
        return None

    async def _first_matching(
        self, metadata: dict[str, str]
    ) -> ProviderInstance | None:
        with sdk_errors():
            matches = await _every_page(
                self._sdk.list(query=self._query(metadata=metadata), **self._api())
            )

        # Prefer a running sandbox when several match, so a duplicate left by
        # an earlier failure does not shadow the one actually serving.
        ordered = sorted(
            matches,
            key=lambda info: str(info.state).lower().endswith("running"),
            reverse=True,
        )
        for info in ordered:
            metadata = getattr(info, "metadata", None) or {}
            return ProviderInstance(
                provider_id=info.sandbox_id,
                name=info.sandbox_id,
                volume_name=None,
                running=str(info.state).lower().endswith("running"),
                profile_digest=metadata.get(
                    meta_profile_digest(self._config.metadata_namespace)
                ),
                template=metadata.get(meta_template(self._config.metadata_namespace)),
            )
        return None

    async def _connect(self, provider_id: str):
        """Reach a sandbox, resuming it if it is paused.

        The timeout is not optional, and is held rather than re-sent per call:
        `e2b_connections` owns both rules and the reasons for them.
        """

        async def open() -> object:
            with sdk_errors():
                return await self._sdk.connect(
                    provider_id,
                    timeout=self._config.sandbox_timeout_seconds,
                    **self._api(),
                )

        return await self._connections.get(provider_id, open)

    def _forget(self, provider_id: str) -> None:
        """Drop a held connection, for a sandbox this process has just ended."""
        self._connections.forget(provider_id)
