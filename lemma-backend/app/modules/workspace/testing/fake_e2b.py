"""An in-memory stand-in for the E2B SDK.

Models the behaviours the provider actually leans on: metadata queries are
conjunctive, a paused sandbox still exists and can be reconnected, volumes are
addressed by id but found by name, and commands stream output through
callbacks rather than returning it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote

import httpx


class FakeE2BError(Exception):
    """Base for SDK-shaped failures the provider has to classify."""


class NotFoundException(FakeE2BError):
    pass


class RateLimitException(FakeE2BError):
    pass


class AuthenticationException(FakeE2BError):
    pass


@dataclass
class FakeSandboxInfo:
    sandbox_id: str
    metadata: dict[str, str]
    state: str = "running"
    template_id: str = "workspace"


@dataclass
class FakeSandboxQuery:
    """Stands in for `e2b.sandbox.sandbox_api.SandboxQuery`.

    Only the metadata filter matters to the provider, and matching the real
    type's shape here is what lets the unit tests run with no e2b extra
    installed -- which is the same configuration a Docker-only deployment ships.
    """

    metadata: dict[str, str] | None = None


@dataclass
class FakePtySize:
    """Stands in for `e2b.sandbox.commands.command_handle.PtySize`."""

    rows: int
    cols: int


class FakeSandboxSdk:
    """Base for anything standing in for `e2b.AsyncSandbox`.

    Carries the types the provider reaches for through the SDK seam, so an
    ad-hoc stand-in written for one test -- an SDK whose `list` only raises,
    say -- still satisfies the provider without importing the real package.
    """

    query_type = FakeSandboxQuery
    pty_size_type = FakePtySize


@dataclass
class FakeVolumeInfo:
    volume_id: str
    name: str


@dataclass
class FakeCommandHandle:
    pid: int


@dataclass
class FakeE2B:
    """The shared world every fake sandbox handle reads and writes."""

    sandboxes: dict[str, FakeSandboxInfo] = field(default_factory=dict)
    volumes: dict[str, FakeVolumeInfo] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    paused: list[str] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    # The directory each command was started in, in the order they started.
    # Recorded because E2B answers a `cwd=None` by starting the process in the
    # image's own default -- a real directory, so the command succeeds and a
    # fake that dropped the argument could not tell "the provider said where"
    # from "the provider said nothing". It said nothing for `execute_python`,
    # and that is precisely how the interpreter ended up in `/workspace` while
    # the shell was in the conversation's directory.
    command_cwds: list[str | None] = field(default_factory=list)
    pause_kept_memory: list[bool] = field(default_factory=list)
    #: What the runtime port answers. 404 is a healthy runtime -- route absent,
    #: port listening -- and 502 is the sandbox whose runtime process has died
    #: while the VM keeps running, which is the state readiness must now catch.
    runtime_status: int = 404
    #: Whether the sandbox agent answers a command. Workspace readiness is a
    #: smoke command through the agent -- there is no runtime port to probe --
    #: so a workspace whose agent is down is modelled here, not with
    #: `runtime_status`.
    agent_answers: bool = True
    # The lifetime each started process was given, in the order they started.
    # Recorded because E2B kills a command at this value and defaults it to 60s,
    # so "the provider passed no timeout" is indistinguishable from "the
    # provider asked for a minute" unless a test can see the argument.
    process_timeouts: list[float | None] = field(default_factory=list)
    # The lifecycle each sandbox was created with, and the timeout each connect
    # asked for. Both are recorded for the same reason as `process_timeouts`:
    # E2B defaults them to values that destroy things -- `on_timeout: "kill"`
    # and a five-minute lease -- so a fake that quietly accepted whatever it was
    # given could not tell "the provider asked for this" from "the provider
    # passed nothing". It did accept them, into `**_kwargs`, for the whole time
    # the provider was passing neither.
    created_lifecycles: list[Any] = field(default_factory=list)
    connect_timeouts: list[float | None] = field(default_factory=list)
    #: Whether each create asked for public traffic. Recorded for the same
    #: reason as the lifecycles above: E2B defaults it to *open*, so a fake that
    #: swallowed the argument could not tell "the provider closed this" from
    #: "the provider said nothing" -- and saying nothing is what puts a signed-in
    #: browser on a public URL.
    created_public_traffic: list[bool | None] = field(default_factory=list)
    #: Every keyword `create` was called with, so a test can bind them
    #: against the real SDK's signature rather than against this stand-in's.
    created_kwargs: list[dict[str, object]] = field(default_factory=list)
    #: The per-sandbox traffic token E2B mints when a sandbox is created with
    #: public traffic disabled. `None` models the older, open arrangement, which
    #: is what every sandbox created before that flag still has -- and what the
    #: provider must report as `public` rather than quietly assume away.
    traffic_access_token: str | None = None
    # Small, so every listing test crosses a page boundary.
    list_page_size: int = 2
    _next: int = 0

    def sandbox_class(self):
        world = self

        class _Paginator:
            """Pages, like the real one.

            It used to hand back everything in a single `next_items()` and had
            no `has_next` at all, so a provider that read one page looked
            complete against it and truncated against the real service. Two
            listings did exactly that, and for adoption the consequence is a
            second sandbox created for an identity that already has one --
            stranding the user's files in the first. The page size is small on
            purpose: pagination should be exercised by ordinary tests, not only
            by ones written to think about it.
            """

            def __init__(self, items, page_size: int):
                self._items = list(items)
                self._page_size = max(1, page_size)
                self._offset = 0
                self._served_any = False

            @property
            def has_next(self) -> bool:
                if self._offset < len(self._items):
                    return True
                # One empty page for an empty listing, so a caller that drains
                # gets the same "nothing here" a first read would have given.
                return not self._served_any

            async def next_items(self):
                page = self._items[self._offset : self._offset + self._page_size]
                self._offset += self._page_size
                self._served_any = True
                return page

        class _Commands:
            def __init__(self, sandbox_id: str):
                self._sandbox_id = sandbox_id

            async def run(
                self,
                cmd,
                background=None,
                envs=None,
                user=None,
                cwd=None,
                on_stdout=None,
                on_stderr=None,
                timeout=None,
                **_kwargs,
            ):
                if not world.agent_answers:
                    raise FakeE2BError("the sandbox agent is not answering")
                world.commands.append(cmd)
                world.command_cwds.append(cwd)
                world.process_timeouts.append(timeout)
                if on_stdout is not None:
                    await on_stdout(f"ran: {cmd}")
                if background:
                    world._next += 1
                    return FakeCommandHandle(pid=world._next)

                class _Result:
                    stdout = f"ran: {cmd}"
                    stderr = ""
                    exit_code = 0
                    error = None

                return _Result()

            async def send_stdin(self, pid, data, **_kwargs):
                return None

            async def kill(self, pid, **_kwargs):
                return True

        class _Files:
            async def get_info(self, path, **_kwargs):
                if path not in world.files:
                    raise NotFoundException(f"{path} not found")
                return _Entry(path, len(world.files[path]))

            async def list(self, path, **_kwargs):
                return [
                    _Entry(name, len(data))
                    for name, data in world.files.items()
                    if name.startswith(path.rstrip("/") + "/")
                ]

            async def read(self, path, format="text", **_kwargs):
                if path not in world.files:
                    raise NotFoundException(f"{path} not found")
                return world.files[path]

            async def write(self, path, data, **_kwargs):
                payload = data.encode() if isinstance(data, str) else bytes(data)
                world.files[path] = payload
                return _Entry(path, len(payload))

            async def remove(self, path, **_kwargs):
                if path not in world.files:
                    raise NotFoundException(f"{path} not found")
                del world.files[path]

            async def rename(self, old_path, new_path, **_kwargs):
                if old_path not in world.files:
                    raise NotFoundException(f"{old_path} not found")
                world.files[new_path] = world.files.pop(old_path)
                return _Entry(new_path, len(world.files[new_path]))

            async def make_dir(self, path, **_kwargs):
                return True

        class _Pty:
            async def create(
                self,
                size,
                on_data=None,
                cwd=None,
                envs=None,
                timeout=None,
                **_kwargs,
            ):
                world._next += 1
                world.command_cwds.append(cwd)
                world.process_timeouts.append(timeout)
                if on_data is not None:
                    await on_data(b"$ ")
                return FakeCommandHandle(pid=world._next)

            async def send_stdin(self, pid, data, **_kwargs):
                world.commands.append(data.decode().strip())
                return

            async def resize(self, pid, size, **_kwargs):
                return None

            async def kill(self, pid, **_kwargs):
                return True

        class FakeAsyncSandbox(FakeSandboxSdk):
            def __init__(self, sandbox_id: str):
                self.sandbox_id = sandbox_id
                self.commands = _Commands(sandbox_id)
                self.files = _Files()
                self.pty = _Pty()

            def download_url(self, path, **_kwargs):
                """Where envd serves this file. Sync in the real SDK too."""
                return f"{FAKE_ENVD_ORIGIN}/files?path={quote(path)}"

            @staticmethod
            async def create(
                template=None,
                timeout=None,
                metadata=None,
                envs=None,
                secure=True,
                allow_internet_access=True,
                mcp=None,
                network=None,
                lifecycle=None,
                volume_mounts=None,
                logger=None,
                # `AsyncSandbox.create` takes its remaining keywords as
                # `Unpack[ApiParams]` and passes them to `ConnectionConfig`,
                # which raises `TypeError` on a name it does not know. Spelling
                # those names out here instead of a `**_kwargs` catch-all is
                # what makes this double fail the way production fails: the
                # previous signature invented an `allow_public_traffic`
                # parameter the SDK has never had at any version, so the test
                # asserting sandboxes were closed passed for months against a
                # call that could only ever have raised.
                api_key=None,
                api_url=None,
                api_headers=None,
                domain=None,
                debug=None,
                headers=None,
                proxy=None,
                request_timeout=None,
                sandbox_url=None,
                validate_api_key=None,
            ):
                world.created_public_traffic.append(
                    (network or {}).get("allow_public_traffic")
                )
                world.created_kwargs.append(
                    {
                        "template": template,
                        "timeout": timeout,
                        "metadata": metadata,
                        "envs": envs,
                        "secure": secure,
                        "allow_internet_access": allow_internet_access,
                        "mcp": mcp,
                        "network": network,
                        "lifecycle": lifecycle,
                        "volume_mounts": volume_mounts,
                        "logger": logger,
                        "api_key": api_key,
                        "domain": domain,
                    }
                )
                world._next += 1
                sandbox_id = f"e2b-{world._next}"
                world.sandboxes[sandbox_id] = FakeSandboxInfo(
                    sandbox_id=sandbox_id, metadata=dict(metadata or {})
                )
                world.created.append(
                    {
                        "template": template,
                        "metadata": dict(metadata or {}),
                        "volume_mounts": volume_mounts,
                        "envs": envs,
                        "lifecycle": lifecycle,
                        "timeout": timeout,
                    }
                )
                world.created_lifecycles.append(lifecycle)
                return FakeAsyncSandbox(sandbox_id)

            @staticmethod
            async def connect(sandbox_id, timeout=None, **_kwargs):
                world.connect_timeouts.append(timeout)
                if sandbox_id not in world.sandboxes:
                    raise NotFoundException(f"sandbox {sandbox_id} not found")
                # Reconnecting resumes a paused sandbox, as the real SDK does.
                world.sandboxes[sandbox_id].state = "running"
                return FakeAsyncSandbox(sandbox_id)

            @staticmethod
            def list(query=None, **_kwargs):
                wanted = dict(getattr(query, "metadata", None) or {})
                return _Paginator(
                    page_size=world.list_page_size,
                    items=[
                        info
                        for info in world.sandboxes.values()
                        if all(info.metadata.get(k) == v for k, v in wanted.items())
                    ],
                )

            async def is_running(self, **_kwargs):
                return world.sandboxes[self.sandbox_id].state == "running"

            async def kill(self, **_kwargs):
                world.killed.append(self.sandbox_id)
                world.sandboxes.pop(self.sandbox_id, None)
                return True

            async def pause(self, keep_memory=True, **_kwargs):
                # Recorded because the default is the bug: a workspace pause
                # that keeps memory restores whatever was running into the
                # next conversation.
                world.pause_kept_memory.append(keep_memory)
                world.paused.append(self.sandbox_id)
                world.sandboxes[self.sandbox_id].state = "paused"
                return True

            async def beta_pause(self, keep_memory=True, **_kwargs):
                # Kept because the real SDK still carries it, deprecated, and a
                # double that drops a method the provider might call would
                # certify a call that no longer exists. It delegates rather
                # than duplicating, so the two can never disagree about what a
                # pause records.
                return await self.pause(keep_memory=keep_memory, **_kwargs)

            def get_host(self, port):
                return f"{port}-{self.sandbox_id}.e2b.test"

            @property
            def traffic_access_token(self):
                return world.traffic_access_token

            def runtime_status(self, port):
                """What the runtime port would answer, without serving HTTP.

                Readiness now probes the runtime rather than trusting
                `is_running()`, because a VM outlives the process it was started
                for. A fake sandbox has no port to probe, so it declares the
                answer: 404 is what a healthy runtime gives (route absent, port
                listening) and what `world.runtime_status` can be set away from
                to model the sandbox whose runtime has died.
                """
                del port
                return world.runtime_status

        return FakeAsyncSandbox

    def volume_class(self):
        world = self

        class FakeAsyncVolume:
            def __init__(self, volume_id: str, name: str):
                self.volume_id = volume_id
                self.name = name

            @staticmethod
            async def list(**_kwargs):
                return list(world.volumes.values())

            @staticmethod
            async def create(name, **_kwargs):
                world._next += 1
                volume_id = f"vol-{world._next}"
                world.volumes[volume_id] = FakeVolumeInfo(
                    volume_id=volume_id, name=name
                )
                return FakeAsyncVolume(volume_id, name)

            @staticmethod
            async def connect(volume_id, **_kwargs):
                info = world.volumes.get(volume_id)
                if info is None:
                    raise NotFoundException(f"volume {volume_id} not found")
                return FakeAsyncVolume(info.volume_id, info.name)

            @staticmethod
            async def destroy(volume_id, **_kwargs):
                return world.volumes.pop(volume_id, None) is not None

        return FakeAsyncVolume


#: The origin `download_url` points at. Nothing resolves it; `envd_transport`
#: is what answers.
FAKE_ENVD_ORIGIN = "https://fake-envd.invalid"


def envd_transport(world: FakeE2B) -> httpx.MockTransport:
    """envd's file route, including the part the provider now depends on.

    Range support is the reason this exists rather than a fake that returns
    whole files. It is written from a measurement against a real sandbox,
    not from the spec: `Range: bytes=1048576-2097151` against a 20 MiB file
    answered `206` with `Content-Range: bytes 1048576-2097151/20971520`,
    `Accept-Ranges: bytes`, and exactly 1048576 bytes. A double that served
    whole files would certify the provider against the behaviour it was
    written to stop using.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.params.get("path", ""))
        if path not in world.files:
            return httpx.Response(404)
        content = world.files[path]
        header = request.headers.get("Range")
        if not header:
            return httpx.Response(200, content=content)
        spec = header.removeprefix("bytes=")
        first, _, last = spec.partition("-")
        start = int(first)
        if start >= len(content):
            return httpx.Response(416)
        end = min(int(last) if last else len(content) - 1, len(content) - 1)
        return httpx.Response(
            206,
            content=content[start : end + 1],
            headers={
                "Content-Range": f"bytes {start}-{end}/{len(content)}",
                "Accept-Ranges": "bytes",
            },
        )

    return httpx.MockTransport(handle)


def envd_client_class(world: FakeE2B) -> type[httpx.AsyncClient]:
    """An `httpx.AsyncClient` wired to `envd_transport`, for monkeypatching.

    The provider builds its own client, as it must -- the URL is signed per
    file and there is nothing to inject. So the substitution happens at the
    class, and the real client code path runs.
    """
    transport = envd_transport(world)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    return _Client


@dataclass
class _Entry:
    path: str
    size: int
    type: str = "file"
    mode: int = 0o644
    modified_time: Any = None
