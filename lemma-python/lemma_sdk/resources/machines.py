"""Machines: the computers agents, functions and people run commands on.

A machine is addressed by a *ref*, never a bare name, because the same name can
mean a pod's machine or one of your own:

- ``me`` -- your default machine, created the first time it is named;
- ``pod:<pod-id>/<name>`` -- a pod's machine (``pod:<name>`` for the pod this
  client is bound to);
- ``mine:<name>`` -- another machine of yours, such as a connected Mac;
- the machine's id.

Refs travel in one URL path segment, where a ``/`` cannot, so they are sent in
the colon form the server also accepts (``pod:<pod-id>:<name>``).
"""

from __future__ import annotations

import contextlib
import json
import ssl
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Literal, Protocol
from urllib.parse import quote
from uuid import UUID

from ..errors import LemmaAPIError, LemmaConfigError
from ..openapi_client.api.machine_browser import (
    machine_browser_access,
    machine_browser_beacon,
    machine_browser_command,
    machine_browser_current_page_url,
    machine_browser_display,
    machine_browser_navigate,
    machine_browser_status,
)
from ..openapi_client.api.machine_consents import (
    machine_consent_grant,
    machine_consent_list,
    machine_consent_revoke,
)
from ..openapi_client.api.machine_files import (
    machine_files_content,
    machine_files_delete,
    machine_files_list,
    machine_files_move,
    machine_files_stat,
    machine_files_sync,
    machine_files_write,
)
from ..openapi_client.api.machine_logins import (
    machine_browser_logins_forget,
    machine_browser_logins_list,
)
from ..openapi_client.api.machine_previews import (
    machine_preview_create,
    machine_preview_list,
    machine_preview_open,
    machine_preview_revoke,
)
from ..openapi_client.api.machine_processes import (
    machine_process_get,
    machine_process_input,
    machine_process_kill,
    machine_process_list,
    machine_process_output,
    machine_process_resize,
    machine_process_start,
)
from ..openapi_client.api.machines import (
    machine_get,
    machine_list,
    machine_pod_create,
    machine_pod_delete,
    machine_pod_grants_get,
    machine_pod_grants_set,
    machine_pod_update,
    machine_policy,
    machine_release,
    machine_reset,
    machine_restart,
    machine_startup,
)
from ..openapi_client.models.browser_access_request import BrowserAccessRequest
from ..openapi_client.models.browser_command_request import BrowserCommandRequest
from ..openapi_client.models.stream_beacon import StreamBeacon
from ..openapi_client.models.create_pod_machine_request import (
    CreatePodMachineRequest,
)
from ..openapi_client.models.create_preview_request import CreatePreviewRequest
from ..openapi_client.models.display_size_request import DisplaySizeRequest
from ..openapi_client.models.grant_consent_request import GrantConsentRequest
from ..openapi_client.models.machine_file_entry import MachineFileEntry
from ..openapi_client.models.machine_grants_model import MachineGrantsModel
from ..openapi_client.models.move_request import MoveRequest
from ..openapi_client.models.navigate_request import NavigateRequest
from ..openapi_client.models.process_input_request import ProcessInputRequest
from ..openapi_client.models.process_output_response import ProcessOutputResponse
from ..openapi_client.models.process_response import ProcessResponse
from ..openapi_client.models.reset_request import ResetRequest
from ..openapi_client.models.start_process_request import StartProcessRequest
from ..openapi_client.models.sync_request import SyncRequest
from ..openapi_client.models.terminal_size_model import TerminalSizeModel
from ..openapi_client.models.update_pod_machine_request import (
    UpdatePodMachineRequest,
)
from ..transport import LemmaTransport
from .base import Resource, as_uuid, compact

if TYPE_CHECKING:
    from ..openapi_client.models.browser_access_response import (
        BrowserAccessResponse,
    )
    from ..openapi_client.models.browser_status_response import (
        BrowserStatusResponse,
    )
    from ..openapi_client.models.consent_list_response import ConsentListResponse
    from ..openapi_client.models.consent_response import ConsentResponse
    from ..openapi_client.models.machine_file_list_response import (
        MachineFileListResponse,
    )
    from ..openapi_client.models.machine_list_response import MachineListResponse
    from ..openapi_client.models.machine_policy_response import (
        MachinePolicyResponse,
    )
    from ..openapi_client.models.machine_response import MachineResponse
    from ..openapi_client.models.machine_startup_response import (
        MachineStartupResponse,
    )
    from ..openapi_client.models.preview_list_response import PreviewListResponse
    from ..openapi_client.models.preview_response import PreviewResponse
    from ..openapi_client.models.process_list_response import ProcessListResponse
    from ..openapi_client.models.reset_response import ResetResponse
    from ..openapi_client.models.web_login_list_response import (
        WebLoginListResponse,
    )

#: What ``reset`` can clean. ``home`` and ``docker`` lose work, so the server
#: wants ``confirm=True`` for them.
ResetScope = Literal["processes", "runs", "caches", "browser-session", "docker", "home"]

#: States after which a process produces nothing more.
STOPPED_STATES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})

#: Most bytes one read of a machine file returns (the endpoint's ceiling).
MAX_READ_BYTES = 8 * 1024 * 1024

#: Longest a single output poll waits server-side (the endpoint's ceiling).
_POLL_WAIT_SECONDS = 25.0


def machine_ref(ref: str, *, pod_id: str | UUID | None = None) -> str:
    """The ref as the server wants it in a URL path segment.

    ``pod:<pod>/<name>`` becomes ``pod:<pod>:<name>``, and ``pod:<name>``
    gains ``pod_id`` when one is given, so a client bound to a pod can name its
    machines without repeating the pod.
    """
    text = (ref or "me").strip() or "me"
    if not text.startswith("pod:"):
        return text
    rest = text[len("pod:") :].replace("/", ":", 1)
    if ":" not in rest and pod_id is not None:
        return f"pod:{pod_id}:{rest}"
    return f"pod:{rest}"


def is_stopped(state: str | None, exit_code: int | None) -> bool:
    """Whether a process is over, by either signal the server gives."""
    return exit_code is not None or (state or "") in STOPPED_STATES


@dataclass(frozen=True, slots=True)
class ProcessChunk:
    """One piece of a process's output, in the order it was produced."""

    sequence: int
    channel: str
    data: str


@dataclass(slots=True)
class RunResult:
    """What a finished command printed and how it ended."""

    exit_code: int | None
    state: str
    chunks: list[ProcessChunk] = field(default_factory=list)

    @property
    def output(self) -> str:
        return "".join(chunk.data for chunk in self.chunks)

    @property
    def stdout(self) -> str:
        return "".join(c.data for c in self.chunks if c.channel in ("stdout", "pty"))

    @property
    def stderr(self) -> str:
        return "".join(c.data for c in self.chunks if c.channel == "stderr")

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class _Socket(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


class ProcessStream:
    """A started process's output, as it arrives, plus a way to type into it.

    Iterate it for :class:`ProcessChunk` objects; when iteration ends the
    process is over and :attr:`exit_code` / :attr:`state` say how. Over the
    live socket when the ``websockets`` package is installed (input and
    resizes ride the same socket), by polling the output endpoint otherwise.
    """

    def __init__(
        self,
        processes: MachineProcesses,
        ref: str,
        process: ProcessResponse,
        *,
        socket: _Socket | None = None,
    ) -> None:
        self._processes = processes
        self.ref = ref
        self.process = process
        self._socket = socket
        self._next = 0
        self.exit_code: int | None = None
        self.state: str | None = None

    @property
    def process_id(self) -> str:
        return self.process.id

    @property
    def live(self) -> bool:
        """True when output arrives over the socket rather than by polling."""
        return self._socket is not None

    def __iter__(self) -> Iterator[ProcessChunk]:
        if self._socket is not None:
            yield from self._from_socket(self._socket)
        else:
            yield from self._from_polling()

    def _from_socket(self, socket: _Socket) -> Iterator[ProcessChunk]:
        try:
            while True:
                try:
                    raw = socket.recv()
                except Exception:  # noqa: BLE001 - websockets' close is an exception
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "output":
                    chunk = ProcessChunk(
                        sequence=int(message.get("sequence") or 0),
                        channel=str(message.get("channel") or "stdout"),
                        data=str(message.get("data") or ""),
                    )
                    self._next = chunk.sequence + 1
                    yield chunk
                elif message.get("type") == "exit":
                    self.state = str(message.get("state") or "")
                    code = message.get("exit_code")
                    self.exit_code = int(code) if code is not None else None
                    return
        finally:
            self.close()
        # The socket went away without an exit frame: finish from the
        # endpoint, so a dropped connection never reads as a finished process.
        if self.state is None:
            yield from self._from_polling()

    def _from_polling(self) -> Iterator[ProcessChunk]:
        while True:
            page = self._processes.output(
                self.ref,
                self.process_id,
                after=self._next,
                wait_seconds=_POLL_WAIT_SECONDS,
            )
            for chunk in page.chunks:
                yield ProcessChunk(
                    sequence=chunk.sequence,
                    channel=str(chunk.channel),
                    data=chunk.data,
                )
            self._next = page.next_sequence
            exit_code = page.exit_code if isinstance(page.exit_code, int) else None
            if is_stopped(page.state, exit_code):
                self.state = page.state
                self.exit_code = exit_code
                return

    def send_input(self, data: str) -> None:
        """Type ``data`` into the process."""
        if self._socket is not None:
            self._socket.send(json.dumps({"type": "input", "data": data}))
        else:
            self._processes.send_input(self.ref, self.process_id, data)

    def resize(self, cols: int, rows: int) -> None:
        """Resize the process's terminal (a process started with ``tty``)."""
        if self._socket is not None:
            self._socket.send(
                json.dumps({"type": "resize", "cols": cols, "rows": rows})
            )
        else:
            self._processes.resize(self.ref, self.process_id, cols=cols, rows=rows)

    def kill(self) -> None:
        self._processes.kill(self.ref, self.process_id)

    def close(self) -> None:
        if self._socket is not None:
            with contextlib.suppress(Exception):
                self._socket.close()


class _MachineResource(Resource):
    def __init__(
        self, transport: LemmaTransport, *, pod_id: str | UUID | None = None
    ) -> None:
        super().__init__(transport)
        self.pod_id = pod_id

    def _ref(self, ref: str) -> str:
        return machine_ref(ref, pod_id=self.pod_id)

    def _pod(self, pod_id: str | UUID | None) -> UUID:
        resolved = pod_id if pod_id is not None else self.pod_id
        if resolved is None:
            raise LemmaConfigError("pod_id is required for a pod's machines")
        return as_uuid(resolved)


class MachineProcesses(_MachineResource):
    """Run, watch, type into and stop processes on a machine."""

    def start(
        self,
        ref: str,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        tty: tuple[int, int] | None = None,
        timeout_seconds: int | None = None,
        service: str | None = None,
        restart_on_resume: bool = False,
        run_id: str | None = None,
    ) -> ProcessResponse:
        """Start ``command`` (a shell line) and return at once.

        ``tty=(cols, rows)`` runs it in a terminal, for a shell or anything
        that draws. ``cwd`` must be inside the machine's home; it defaults to
        ``~/lemma``.
        """
        payload: dict[str, object] = compact(
            {
                "command": command,
                "cwd": cwd,
                "env": dict(env) if env else None,
                "tty": {"cols": tty[0], "rows": tty[1]} if tty else None,
                "timeout_seconds": timeout_seconds,
                "service": service,
                "run_id": run_id,
            }
        )
        if restart_on_resume:
            payload["restart_on_resume"] = True
        return self._call(
            machine_process_start,
            self._ref(ref),
            body=StartProcessRequest.from_dict(payload),
        )

    def list(self, ref: str = "me") -> ProcessListResponse:
        return self._call(machine_process_list, self._ref(ref))

    def get(self, ref: str, process_id: str | UUID) -> ProcessResponse:
        return self._call(machine_process_get, self._ref(ref), as_uuid(process_id))

    def output(
        self,
        ref: str,
        process_id: str | UUID,
        *,
        after: int = 0,
        wait_seconds: float = 0.0,
    ) -> ProcessOutputResponse:
        """A page of output after sequence ``after``, waiting up to
        ``wait_seconds`` (at most 30) for some to arrive."""
        return self._call(
            machine_process_output,
            self._ref(ref),
            as_uuid(process_id),
            after=after,
            wait_seconds=wait_seconds,
        )

    def send_input(self, ref: str, process_id: str | UUID, data: str) -> None:
        self._call(
            machine_process_input,
            self._ref(ref),
            as_uuid(process_id),
            body=ProcessInputRequest(data=data),
        )

    def resize(self, ref: str, process_id: str | UUID, *, cols: int, rows: int) -> None:
        self._call(
            machine_process_resize,
            self._ref(ref),
            as_uuid(process_id),
            body=TerminalSizeModel(cols=cols, rows=rows),
        )

    def kill(self, ref: str, process_id: str | UUID) -> None:
        self._call(machine_process_kill, self._ref(ref), as_uuid(process_id))

    def attach(
        self,
        ref: str,
        process: ProcessResponse,
        *,
        via: Literal["auto", "socket", "poll"] = "auto",
    ) -> ProcessStream:
        """Follow a process already started: its output, and its input."""
        wire_ref = self._ref(ref)
        socket = None if via == "poll" else self._open_socket(wire_ref, process.id, via)
        return ProcessStream(self, wire_ref, process, socket=socket)

    def _open_socket(
        self, wire_ref: str, process_id: str, via: Literal["auto", "socket"]
    ) -> _Socket | None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            if via == "socket":
                raise ImportError(
                    'Streaming a process needs the websockets package: pip install "lemma-sdk[live]"'
                ) from None
            return None
        generated = self._transport.generated
        url = process_stream_url(str(generated._base_url), wire_ref, process_id)
        ssl_option: ssl.SSLContext | None = None
        if url.startswith("wss://") and generated._verify_ssl is False:
            ssl_option = ssl.create_default_context()
            ssl_option.check_hostname = False
            ssl_option.verify_mode = ssl.CERT_NONE
        stack = contextlib.ExitStack()
        try:
            connection = stack.enter_context(
                connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {generated.token}"},
                    ssl=ssl_option,
                    open_timeout=self._transport.timeout,
                    max_size=None,
                )
            )
        except Exception:
            if via == "socket":
                raise
            # A proxy that refuses upgrades still lets the endpoint answer.
            return None
        return _EnteredSocket(connection, stack)


class _EnteredSocket:
    """A sync websocket held open the way the library wants: entered."""

    def __init__(self, connection: _Socket, stack: contextlib.ExitStack) -> None:
        self._connection = connection
        self._stack = stack

    def send(self, message: str) -> None:
        self._connection.send(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        return self._connection.recv(timeout)

    def close(self) -> None:
        self._stack.close()


def process_stream_url(base_url: str, wire_ref: str, process_id: str) -> str:
    """The live socket for one process, on the API host."""
    root = base_url.rstrip("/")
    if root.startswith("https://"):
        root = "wss://" + root.removeprefix("https://")
    elif root.startswith("http://"):
        root = "ws://" + root.removeprefix("http://")
    return (
        f"{root}/machines/{quote(wire_ref, safe='')}/processes/"
        f"{quote(str(process_id), safe='')}/stream"
    )


class MachineFiles(_MachineResource):
    """Files in a machine's home."""

    def list(
        self,
        ref: str,
        path: str | None = None,
        *,
        after: str | None = None,
        wake: bool = False,
    ) -> MachineFileListResponse:
        """One directory, a page at a time (``after`` is the previous page's
        cursor). ``wake`` starts a stopped machine rather than answering
        from what is known without it."""
        return self._call(
            machine_files_list,
            self._ref(ref),
            **compact({"path": path, "after": after}),
            wake=wake,
        )

    def stat(self, ref: str, path: str) -> MachineFileEntry:
        return self._call(machine_files_stat, self._ref(ref), path=path)

    def read(self, ref: str, path: str) -> bytes:
        """The whole file's bytes. Large files are fetched a page at a time;
        :meth:`read_stream` or :meth:`download_to` never hold more than a page."""
        return b"".join(self.read_stream(ref, path))

    def read_range(self, ref: str, path: str, *, offset: int, length: int) -> bytes:
        return b"".join(self.read_stream(ref, path, offset=offset, length=length))

    def read_stream(
        self,
        ref: str,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> Iterator[bytes]:
        """The file's bytes in chunks, from ``offset``.

        One response carries at most :data:`MAX_READ_BYTES`, so without a
        ``length`` this keeps asking until a page comes back short.
        """
        remaining = length
        position = offset
        while remaining is None or remaining > 0:
            page = (
                MAX_READ_BYTES if remaining is None else min(remaining, MAX_READ_BYTES)
            )
            received = 0
            for chunk in self._page(ref, path, offset=position, length=page):
                received += len(chunk)
                yield chunk
            position += received
            if remaining is not None:
                remaining -= received
            if received < page:
                return

    def _page(
        self, ref: str, path: str, *, offset: int, length: int
    ) -> Iterator[bytes]:
        response = self._stream(
            machine_files_content,
            self._ref(ref),
            path=path,
            offset=offset,
            length=length,
            read_timeout=self._transport.timeout,
        )
        try:
            yield from response.iter_bytes()
        finally:
            response.close()

    def download_to(self, ref: str, path: str, local_path: str | Path) -> Path:
        """Save a machine file to ``local_path``, a chunk at a time."""
        target = Path(local_path)
        with target.open("wb") as handle:
            for chunk in self.read_stream(ref, path):
                handle.write(chunk)
        return target

    def write(
        self, ref: str, path: str, data: bytes | str | IO[bytes]
    ) -> MachineFileEntry:
        """Create or replace a file (at most 64 MB). Parent directories are
        created."""
        content: bytes | Iterator[bytes]
        if isinstance(data, str):
            content = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            content = bytes(data)
        else:
            content = _chunks(data)
        request = machine_files_write._get_kwargs(self._ref(ref), path=path)
        response = self.generated.get_httpx_client().request(
            **request,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
            timeout=max(self._transport.timeout, 600.0),
        )
        if response.status_code >= 400:
            raise self._transport.error_from_response(
                response.status_code, None, response.content, response.headers
            )
        return MachineFileEntry.from_dict(response.json())

    def delete(self, ref: str, path: str, *, recursive: bool = False) -> None:
        self._call(machine_files_delete, self._ref(ref), path=path, recursive=recursive)

    def move(self, ref: str, source: str, destination: str) -> MachineFileEntry:
        return self._call(
            machine_files_move,
            self._ref(ref),
            body=MoveRequest(source=source, destination=destination),
        )

    def sync(
        self,
        ref: str,
        *,
        machine_path: str,
        pod_path: str,
        direction: Literal["to_pod", "from_pod"],
        pod_id: str | UUID | None = None,
    ) -> object:
        """Copy between the machine and a pod's files, either way."""
        return self._call(
            machine_files_sync,
            self._ref(ref),
            body=SyncRequest.from_dict(
                {
                    "machine_path": machine_path,
                    "pod_path": pod_path,
                    "direction": direction,
                    "pod_id": str(self._pod(pod_id)),
                }
            ),
        )


def _chunks(handle: IO[bytes], size: int = 1024 * 1024) -> Iterator[bytes]:
    while chunk := handle.read(size):
        yield chunk


class MachinePreviews(_MachineResource):
    """URLs for a port a machine is serving on."""

    def create(
        self,
        ref: str,
        port: int,
        *,
        visibility: Literal["private", "link"] | None = None,
        ttl_seconds: int | None = None,
    ) -> PreviewResponse:
        """A ``private`` preview opens only for people who may use the
        machine; a ``link`` preview opens for anyone holding the URL."""
        return self._call(
            machine_preview_create,
            self._ref(ref),
            body=CreatePreviewRequest.from_dict(
                compact(
                    {"port": port, "visibility": visibility, "ttl_seconds": ttl_seconds}
                )
            ),
        )

    def list(self, ref: str = "me") -> PreviewListResponse:
        return self._call(machine_preview_list, self._ref(ref))

    def revoke(self, ref: str, preview_id: str | UUID) -> None:
        self._call(machine_preview_revoke, self._ref(ref), as_uuid(preview_id))

    def open_url(self, label: str) -> str:
        """Where opening a private preview sends a signed-in person.

        The endpoint is a browser redirect; this reads the redirect instead of
        following it, so a script can hand the URL to a browser.
        """
        request = machine_preview_open._get_kwargs(label)
        response = self.generated.get_httpx_client().request(
            **request, follow_redirects=False
        )
        if response.status_code >= 400:
            raise self._transport.error_from_response(
                response.status_code, None, response.content, response.headers
            )
        location = response.headers.get("location")
        if not location:
            raise LemmaAPIError(
                status_code=response.status_code,
                message="The preview did not redirect anywhere",
            )
        return location


class MachineBrowser(_MachineResource):
    """The browser on a machine."""

    def status(self, ref: str = "me") -> BrowserStatusResponse:
        return self._call(machine_browser_status, self._ref(ref))

    def current_page_url(self, ref: str, *, origin: str) -> object:
        return self._call(
            machine_browser_current_page_url, self._ref(ref), origin=origin
        )

    def display(self, ref: str, *, width: int, height: int) -> object:
        return self._call(
            machine_browser_display,
            self._ref(ref),
            body=DisplaySizeRequest(width=width, height=height),
        )

    def access(
        self, ref: str = "me", *, ttl_seconds: int | None = None
    ) -> BrowserAccessResponse:
        """A short-lived URL that shows the browser to a person."""
        body = (
            BrowserAccessRequest(ttl_seconds=ttl_seconds)
            if ttl_seconds is not None
            else BrowserAccessRequest()
        )
        return self._call(machine_browser_access, self._ref(ref), body=body)

    def navigate(self, ref: str, url: str) -> object:
        return self._call(
            machine_browser_navigate, self._ref(ref), body=NavigateRequest(url=url)
        )

    def command(self, ref: str, action: dict[str, str]) -> object:
        """One of the toolbar's browser commands, e.g. ``{"command": "back"}``
        or ``{"command": "navigate", "url": "https://example.com"}``; the open
        tabs afterwards. The set is closed: back, forward, reload, navigate,
        tabs, activate, close."""
        return self._call(
            machine_browser_command,
            self._ref(ref),
            body=BrowserCommandRequest.from_dict({"action": action}),
        )

    def beacon(self, ref: str, report: dict[str, object]) -> object:
        """Report how a live screen session went (transport, fps, latency)."""
        return self._call(
            machine_browser_beacon, self._ref(ref), body=StreamBeacon.from_dict(report)
        )

    def logins(self, ref: str = "me", *, wake: bool = False) -> WebLoginListResponse:
        """Sites the machine's browser is signed in to."""
        return self._call(machine_browser_logins_list, self._ref(ref), wake=wake)

    def forget_login(self, ref: str, site: str) -> object:
        """Sign the machine's browser out of ``site``."""
        return self._call(machine_browser_logins_forget, self._ref(ref), site)


class MachineConsents(Resource):
    """Apps you have let start processes on your machines."""

    def list(self) -> ConsentListResponse:
        return self._call(machine_consent_list)

    def grant(self, app_id: str | UUID) -> ConsentResponse:
        return self._call(
            machine_consent_grant, body=GrantConsentRequest(app_id=as_uuid(app_id))
        )

    def revoke(self, app_id: str | UUID) -> None:
        self._call(machine_consent_revoke, as_uuid(app_id))


class Machines(_MachineResource):
    """Machines you can use: your own, and your pods'.

    ``pod_id`` is the pod ``pod:<name>`` refers to, and the default for the
    pod-machine management calls.
    """

    @property
    def processes(self) -> MachineProcesses:
        return MachineProcesses(self._transport, pod_id=self.pod_id)

    @property
    def files(self) -> MachineFiles:
        return MachineFiles(self._transport, pod_id=self.pod_id)

    @property
    def previews(self) -> MachinePreviews:
        return MachinePreviews(self._transport, pod_id=self.pod_id)

    @property
    def browser(self) -> MachineBrowser:
        return MachineBrowser(self._transport, pod_id=self.pod_id)

    @property
    def consents(self) -> MachineConsents:
        return MachineConsents(self._transport)

    # -- the machine itself ------------------------------------------------

    def list(self, *, pod_id: str | UUID | None = None) -> MachineListResponse:
        """Every machine you can see; only one pod's with ``pod_id``."""
        return self._call(
            machine_list, **compact({"pod_id": as_uuid(pod_id) if pod_id else None})
        )

    def get(self, ref: str = "me") -> MachineResponse:
        return self._call(machine_get, self._ref(ref))

    def startup(self, ref: str = "me") -> MachineStartupResponse:
        """How far a starting machine has got."""
        return self._call(machine_startup, self._ref(ref))

    def release(self, ref: str = "me") -> object:
        """Let the machine pause: nothing of yours needs it running."""
        return self._call(machine_release, self._ref(ref))

    def restart(self, ref: str = "me") -> object:
        return self._call(machine_restart, self._ref(ref))

    def reset(
        self,
        ref: str,
        scopes: Sequence[ResetScope],
        *,
        confirm: bool = False,
        forget_logins: bool = False,
    ) -> ResetResponse:
        """Clean the machine by scope. ``home`` and ``docker`` discard work
        and need ``confirm=True``; ``forget_logins`` also signs the browser
        out of every site."""
        return self._call(
            machine_reset,
            self._ref(ref),
            body=ResetRequest.from_dict(
                {
                    "scopes": list(scopes),
                    "confirm": confirm,
                    "forget_logins": forget_logins,
                }
            ),
        )

    def policy(self, *, pod_id: str | UUID | None = None) -> MachinePolicyResponse:
        """What your plan (or the pod's) allows: sizes, templates, limits."""
        return self._call(
            machine_policy, **compact({"pod_id": as_uuid(pod_id) if pod_id else None})
        )

    # -- a pod's machines --------------------------------------------------

    def create(
        self,
        name: str,
        *,
        pod_id: str | UUID | None = None,
        template: Literal["workspace", "workspace-docker"] | None = None,
        size: str | None = None,
        visibility: Literal["pod", "restricted"] | None = None,
        run_slots: int | None = None,
        display_name: str | None = None,
    ) -> MachineResponse:
        """Create a machine for a pod."""
        return self._call(
            machine_pod_create,
            self._pod(pod_id),
            body=CreatePodMachineRequest.from_dict(
                compact(
                    {
                        "name": name,
                        "template": template,
                        "size": size,
                        "visibility": visibility,
                        "run_slots": run_slots,
                        "display_name": display_name,
                    }
                )
            ),
        )

    def update(
        self,
        name: str,
        *,
        pod_id: str | UUID | None = None,
        size: str | None = None,
        visibility: Literal["pod", "restricted"] | None = None,
        run_slots: int | None = None,
        display_name: str | None = None,
    ) -> MachineResponse:
        return self._call(
            machine_pod_update,
            self._pod(pod_id),
            name,
            body=UpdatePodMachineRequest.from_dict(
                compact(
                    {
                        "size": size,
                        "visibility": visibility,
                        "run_slots": run_slots,
                        "display_name": display_name,
                    }
                )
            ),
        )

    def delete(self, name: str, *, pod_id: str | UUID | None = None) -> None:
        self._call(machine_pod_delete, self._pod(pod_id), name)

    def grants(
        self, name: str, *, pod_id: str | UUID | None = None
    ) -> MachineGrantsModel:
        """Who may use a restricted pod machine."""
        return self._call(machine_pod_grants_get, self._pod(pod_id), name)

    def set_grants(
        self,
        name: str,
        *,
        pod_id: str | UUID | None = None,
        members: Sequence[str | UUID] = (),
        agents: Sequence[str] = (),
        functions: Sequence[str] = (),
    ) -> MachineGrantsModel:
        """Replace who may use a restricted pod machine."""
        return self._call(
            machine_pod_grants_set,
            self._pod(pod_id),
            name,
            body=MachineGrantsModel(
                members=[as_uuid(member) for member in members],
                agents=list(agents),
                functions=list(functions),
            ),
        )

    # -- running commands ----------------------------------------------------

    def exec(
        self,
        ref: str,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        tty: tuple[int, int] | None = None,
        timeout_seconds: int | None = None,
        via: Literal["auto", "socket", "poll"] = "auto",
    ) -> ProcessStream:
        """Start ``command`` and follow its output as it is produced.

        ::

            stream = lemma.machines.exec("me", "npm test")
            for chunk in stream:
                print(chunk.data, end="")
            print("exit", stream.exit_code)
        """
        processes = self.processes
        process = processes.start(
            ref, command, cwd=cwd, env=env, tty=tty, timeout_seconds=timeout_seconds
        )
        return processes.attach(ref, process, via=via)

    def run(
        self,
        ref: str,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int | None = None,
        via: Literal["auto", "socket", "poll"] = "auto",
    ) -> RunResult:
        """Run ``command`` to the end and return its exit code and output."""
        stream = self.exec(
            ref, command, cwd=cwd, env=env, timeout_seconds=timeout_seconds, via=via
        )
        chunks = list(stream)
        return RunResult(
            exit_code=stream.exit_code, state=stream.state or "", chunks=chunks
        )


__all__ = [
    "MachineBrowser",
    "MachineConsents",
    "MachineFiles",
    "MachinePreviews",
    "MachineProcesses",
    "Machines",
    "ProcessChunk",
    "ProcessStream",
    "RunResult",
    "is_stopped",
    "machine_ref",
    "process_stream_url",
]
