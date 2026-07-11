"""Desktop supervisor: machine-readable lifecycle protocol over stdio.

The desktop shell spawns ``lemma-stack supervise`` (as a bundled sidecar) and
speaks JSON lines: commands arrive on stdin, events leave on stdout. Anything
the orchestration (and its child processes) writes to fd 1/2 is captured and
re-emitted as ``log`` events so stdout stays pure JSONL.

Protocol (v1) — byte-compatible with the previous installer-based supervisor:
  -> {"cmd": "start", "setup": false, "rebuild": false, "id": "..."}
  -> {"cmd": "stop", "infra": false, "id": "..."}
  -> {"cmd": "restart", "id": "..."}
  -> {"cmd": "status", "id": "..."}
  -> {"cmd": "ping", "id": "..."}
  -> {"cmd": "shutdown", "stop_services": false, "id": "..."}

  <- {"v":1,"event":"hello","protocol":1,"pid":123,"phases":[...]}
  <- {"v":1,"event":"ack","cmd":"start","id":"..."}
  <- {"v":1,"event":"phase","key":"infra","label":"...","progress":25,"detail":"..."}
  <- {"v":1,"event":"log","line":"..."}
  <- {"v":1,"event":"state","status":"starting|running|stopping|stopped|error","running":bool}
  <- {"v":1,"event":"provider","provider":"docker"}
  <- {"v":1,"event":"ready","url":"http://127-0-0-1.sslip.io:3711","api_url":"http://127-0-0-1.sslip.io:8711"}
  <- {"v":1,"event":"done","cmd":"start","id":"...","ok":true}
  <- {"v":1,"event":"error","message":"...","id":"..."}
  <- {"v":1,"event":"status","running":bool,"ready":bool,...}
  <- {"v":1,"event":"pong","id":"..."}

stdin EOF means the shell went away: the supervisor exits but leaves services
running (hide-to-tray semantics), unless a prior shutdown asked otherwise.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time

from lemma_stack import __version__
from lemma_stack.config import render, store
from lemma_stack.paths import LocalPaths
from lemma_stack.runtime import detect
from lemma_stack.stack.specs import CONTAINER_PREFIX

PROTOCOL_VERSION = 1

# (label, progress%) — the splash renders these as the startup walkthrough.
PHASES = {
    "boot": ("Booting local services", 4),
    "check": ("Checking local runtime", 8),
    "pull": ("Fetching Lemma", 30),
    "infra": ("Starting local services", 45),
    "migrations": ("Checking workspace data", 60),
    "workspace": ("Preparing workspace runtime", 70),
    "backend": ("Preparing backend", 82),
    "frontend": ("Preparing Lemma", 90),
    "verify": ("Opening Lemma", 95),
    "ready": ("Lemma is ready", 100),
    "stopping": ("Stopping services", 0),
    "stopped": ("Services stopped", 0),
    "error": ("Startup failed", 0),
}

# Which phase each lifecycle service belongs to (for on_progress mapping).
_SERVICE_PHASE = {
    "db": "infra",
    "redis": "infra",
    "supertokens": "infra",
    "kreuzberg": "infra",
    "agentbox": "workspace",
    "backend": "backend",
    "frontend": "frontend",
}


class Supervisor:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run or os.environ.get("LEMMA_SUPERVISE_DRY_RUN") == "1"
        self.paths = LocalPaths()
        self._emit_lock = threading.Lock()
        self._op_lock = threading.Lock()
        self._stop_services_on_exit = False
        self._shutdown = threading.Event()
        self._status = "stopped"
        self._ready = False
        self._out: io.TextIOBase | None = None

    # -- output plumbing ---------------------------------------------------

    def _claim_stdio(self) -> None:
        """Reserve real stdout for JSONL; route fd 1/2 into a log pipe."""
        self._out = os.fdopen(os.dup(1), "w", buffering=1)
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 1)
        os.dup2(write_fd, 2)
        os.close(write_fd)
        sys.stdout = io.TextIOWrapper(os.fdopen(os.dup(1), "wb"), line_buffering=True)
        sys.stderr = io.TextIOWrapper(os.fdopen(os.dup(2), "wb"), line_buffering=True)
        reader = os.fdopen(read_fd, "r", errors="replace")

        def pump() -> None:
            for line in reader:
                line = line.rstrip("\n")
                if line:
                    self.emit("log", line=line)

        threading.Thread(target=pump, daemon=True, name="log-pump").start()

    def emit(self, event: str, **fields: object) -> None:
        payload = {"v": PROTOCOL_VERSION, "event": event, **fields}
        with self._emit_lock:
            assert self._out is not None
            self._out.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._out.flush()

    def _set_phase(self, key: str, detail: str = "") -> None:
        label, progress = PHASES[key]
        self.emit("phase", key=key, label=label, progress=progress, detail=detail)

    def _set_state(self, status: str, *, ready: bool | None = None) -> None:
        self._status = status
        if ready is not None:
            self._ready = ready
        self.emit(
            "state",
            status=status,
            running=status in ("starting", "running", "stopping"),
            ready=self._ready,
        )

    # -- config helpers ------------------------------------------------------

    def _config(self):
        return store.load_or_create(self.paths)

    def _frontend_url(self, config) -> str:
        return render.frontend_origin(config)

    def _backend_url(self, config) -> str:
        return render.backend_origin(config)

    # -- operations ----------------------------------------------------------

    def _op_start(self, *, setup: bool, rebuild: bool) -> None:
        self._set_state("starting", ready=False)
        if self.dry_run:
            for key in ("check", "pull", "infra", "migrations", "workspace", "backend", "frontend", "verify"):
                self._set_phase(key, "dry run")
                time.sleep(0.4)
            self.emit("provider", provider="docker")
            self._finish_ready(self._config())
            return

        from lemma_stack import orchestrate

        self.paths.ensure()
        config = self._config()

        self._set_phase("check", "preparing local runtime")
        provider = self._resolve_provider(config)
        self.emit("provider", provider=provider)

        manifest = orchestrate.resolve_manifest(config, self.paths, prefer_pinned=True)

        def progress(stage: str, detail: str) -> None:
            if stage == "pull":
                self._set_phase("pull", detail)
            elif stage == "migrate":
                self._set_phase("migrations", detail)
            elif stage.startswith("service:"):
                name = stage.split(":", 1)[1]
                self._set_phase(_SERVICE_PHASE.get(name, "infra"), f"starting {name}")
            elif stage == "ready":
                self._set_phase("verify", "verifying services")

        orchestrate.bring_up(
            self.paths,
            config,
            provider=provider,
            manifest=manifest,
            do_register=True,
            progress=progress,
        )
        self._set_phase("workspace", "installing terminal and skills")
        from lemma_stack.register import install_lemma_cli_and_skills

        install_lemma_cli_and_skills(version=manifest.version)
        self._finish_ready(config)

    def _resolve_provider(self, config) -> str:
        """Honor an explicit AGENTBOX_PROVIDER from the desktop, else config,
        else auto-detect (podman preferred). Persist the resolution.

        Always goes through select_runtime so a provider that is requested or
        persisted but not actually installed gets installed (podman) or fails
        with an actionable error (docker) instead of "CLI not found on PATH".
        """
        requested = (os.environ.get("AGENTBOX_PROVIDER") or "").strip().lower()
        if requested not in ("docker", "podman"):
            # store.provider() defaults to podman; only trust it if the user
            # actually made a choice at some point.
            stored = str(config.get("runtime", {}).get("provider", "")).strip().lower()
            requested = stored if stored in ("docker", "podman") else "auto"
        provider = detect.select_runtime(requested, assume_yes=True)
        config.setdefault("runtime", {})
        config["runtime"]["provider"] = provider
        store.save(self.paths, config)
        return provider

    def _finish_ready(self, config) -> None:
        self._set_phase("ready")
        self._set_state("running", ready=True)
        self.emit("ready", url=self._frontend_url(config), api_url=self._backend_url(config))

    def _op_stop(self, *, infra: bool) -> None:
        self._set_state("stopping")
        self._set_phase("stopping", "stopping app services")
        if not self.dry_run:
            from lemma_stack import orchestrate

            config = self._config()
            orchestrate.bring_down(self.paths, config, infra=infra)
        else:
            time.sleep(0.3)
        self._set_phase("stopped")
        self._set_state("stopped", ready=False)
        self.emit("stopped", infra=infra)

    def _op_restart(self) -> None:
        self._op_stop(infra=False)
        self._op_start(setup=False, rebuild=False)

    def _emit_status(self, request_id: object) -> None:
        config = self._config()
        ports = {}
        provider = None
        if not self.dry_run:
            try:
                provider = store.provider(config)
                runtime = detect.ensure_ready(provider)
                for name in ("backend", "frontend", "agentbox"):
                    ports[name] = runtime.container_running(f"{CONTAINER_PREFIX}-{name}")
            except Exception:
                ports = {"backend": False, "frontend": False, "agentbox": False}
        else:
            ports = {"backend": self._ready, "frontend": self._ready, "agentbox": self._ready}
        self.emit(
            "status",
            id=request_id,
            status=self._status,
            ready=self._ready,
            running=self._status in ("starting", "running", "stopping"),
            ports=ports,
            provider=provider,
            url=self._frontend_url(config),
            api_url=self._backend_url(config),
            dry_run=self.dry_run,
        )

    # -- command loop --------------------------------------------------------

    def _run_op(self, name: str, request_id: object, fn, **kwargs) -> None:
        if not self._op_lock.acquire(blocking=False):
            self.emit("error", id=request_id, code="busy",
                      message=f"cannot run {name!r}: another operation is in progress")
            return

        def worker() -> None:
            try:
                fn(**kwargs)
                self.emit("done", cmd=name, id=request_id, ok=True)
            except SystemExit as exc:
                message = str(exc) if str(exc) not in ("", "None") else f"{name} failed"
                self._set_phase("error", message)
                self._set_state("error", ready=False)
                self.emit("error", id=request_id, message=message)
                self.emit("done", cmd=name, id=request_id, ok=False)
            except Exception as exc:  # noqa: BLE001 - protocol boundary
                self._set_phase("error", str(exc))
                self._set_state("error", ready=False)
                self.emit("error", id=request_id, message=f"{type(exc).__name__}: {exc}")
                self.emit("done", cmd=name, id=request_id, ok=False)
            finally:
                self._op_lock.release()

        threading.Thread(target=worker, daemon=True, name=f"op-{name}").start()

    def _dispatch(self, message: dict) -> None:
        cmd = message.get("cmd")
        request_id = message.get("id")
        if cmd not in ("ping", "status"):
            self.emit("ack", cmd=cmd, id=request_id)
        if cmd == "ping":
            self.emit("pong", id=request_id)
        elif cmd == "status":
            self._emit_status(request_id)
        elif cmd == "start":
            self._run_op("start", request_id, self._op_start,
                         setup=bool(message.get("setup")), rebuild=bool(message.get("rebuild")))
        elif cmd == "stop":
            self._run_op("stop", request_id, self._op_stop, infra=bool(message.get("infra")))
        elif cmd == "restart":
            self._run_op("restart", request_id, self._op_restart)
        elif cmd == "shutdown":
            self._stop_services_on_exit = bool(message.get("stop_services"))
            self._shutdown.set()
        else:
            self.emit("error", id=request_id, code="unknown-command",
                      message=f"unknown command {cmd!r}")

    def run(self) -> int:
        self._claim_stdio()
        self.emit(
            "hello",
            protocol=PROTOCOL_VERSION,
            pid=os.getpid(),
            admin_version=__version__,
            dry_run=self.dry_run,
            phases=[
                {"key": key, "label": label, "progress": progress}
                for key, (label, progress) in PHASES.items()
            ],
        )
        self._set_state("stopped")

        def read_stdin() -> None:
            for raw in sys.stdin:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError("expected a JSON object")
                except ValueError as exc:
                    self.emit("error", code="bad-input", message=f"invalid command: {exc}")
                    continue
                try:
                    self._dispatch(message)
                except Exception as exc:  # noqa: BLE001 - protocol boundary
                    self.emit("error", code="dispatch", message=f"{type(exc).__name__}: {exc}")
                if self._shutdown.is_set():
                    break
            self._shutdown.set()

        threading.Thread(target=read_stdin, daemon=True, name="stdin").start()
        self._shutdown.wait()

        if self._stop_services_on_exit:
            with self._op_lock:
                pass  # let any in-flight operation finish first
            try:
                self._op_stop(infra=False)
            except Exception as exc:  # noqa: BLE001
                self.emit("error", message=f"stop on shutdown failed: {exc}")
        self.emit("bye", services_running=not self._stop_services_on_exit)
        return 0


def run_supervisor(*, dry_run: bool = False) -> int:
    return Supervisor(dry_run=dry_run).run()
