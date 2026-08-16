"""An in-memory stand-in for the E2B SDK.

Models the behaviours the provider actually leans on: metadata queries are
conjunctive, a paused sandbox still exists and can be reconnected, volumes are
addressed by id but found by name, and commands stream output through
callbacks rather than returning it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    pause_kept_memory: list[bool] = field(default_factory=list)
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
                world.commands.append(cmd)
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
                world.process_timeouts.append(timeout)
                if on_data is not None:
                    await on_data(b"$ ")
                return FakeCommandHandle(pid=world._next)

            async def send_stdin(self, pid, data, **_kwargs):
                world.commands.append(data.decode().strip())
                return None

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

            @staticmethod
            async def create(
                template=None,
                timeout=None,
                metadata=None,
                envs=None,
                volume_mounts=None,
                lifecycle=None,
                **_kwargs,
            ):
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
                return _Paginator(page_size=world.list_page_size, items=
                    [
                        info
                        for info in world.sandboxes.values()
                        if all(
                            info.metadata.get(k) == v for k, v in wanted.items()
                        )
                    ]
                )

            async def is_running(self, **_kwargs):
                return world.sandboxes[self.sandbox_id].state == "running"

            async def kill(self, **_kwargs):
                world.killed.append(self.sandbox_id)
                world.sandboxes.pop(self.sandbox_id, None)
                return True

            async def beta_pause(self, keep_memory=True, **_kwargs):
                # Recorded because the default is the bug: a workspace pause
                # that keeps memory restores whatever was running into the
                # next conversation.
                world.pause_kept_memory.append(keep_memory)
                world.paused.append(self.sandbox_id)
                world.sandboxes[self.sandbox_id].state = "paused"
                return True

            def get_host(self, port):
                return f"{port}-{self.sandbox_id}.e2b.test"

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


@dataclass
class _Entry:
    path: str
    size: int
    type: str = "file"
    mode: int = 0o644
    modified_time: Any = None
