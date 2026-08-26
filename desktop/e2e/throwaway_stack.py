#!/usr/bin/env python3
"""Stand up a throwaway Lemma stack from this checkout, and take it down again.

The point is to run the desktop journeys against **the code in your working
tree** without touching the install you use. Two bugs shipped to users because
every automated check ran against something that was not the desktop build; this
is the lane where a change can be seen working before it becomes a DMG.

What it is faithful about, and what it borrows:

* The environment is rendered by **this checkout's `lemma-locald`**, not written
  out here. `Daemon::new` renders `host-pack.json` before it starts anything, so
  the daemon is run just long enough to produce one and then stopped. That
  matters: `SESSION_COOKIE_DOMAIN` is the fix, and a harness that restated it
  would pass whether or not locald actually emits it.
* The backend is the **packed** interpreter and layout a release ships, with
  this checkout's `app/` overlaid onto it — so a change is exercised in the
  shape it will actually run in, not under `uv run`.
* Postgres, Redis and SuperTokens are **borrowed** from the running install's
  guest VM, on their own databases. A second VM would need ~4 GiB and a second
  set of container images for infrastructure this cannot affect: the throwaway
  databases are dropped on teardown, and the install's own are never opened.

So: separate root, separate ports, separate databases, separate storage. Shared
guest.

    python3 desktop/e2e/throwaway_stack.py up     # prints JSON on stdout
    python3 desktop/e2e/throwaway_stack.py down

`up` is idempotent-ish only in the sense that it refuses to run over a root that
is already up; run `down` first.
"""

# Runs under whatever `python3` is on PATH -- on macOS that is Xcode's 3.9, not
# the backend's 3.14. So: no PEP 758 unparenthesised `except A, B:`, no match
# statements, nothing newer than 3.9. Do not run `ruff format` over this file
# with the backend's config; it targets 3.14 and rewrites the except clauses
# into syntax this interpreter cannot parse.

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Everything this creates lives under here, and `down` refuses to delete
# anything that is not beneath it. The install being borrowed from is never a
# candidate for removal.
DEFAULT_ROOT = Path("/tmp/lemma-desktop-e2e")

INSTALL_ROOT = Path.home() / "Library" / "Application Support" / "Lemma"
INSTALLED_APP = Path("/Applications/Lemma.app")

# The borrowed Redis keyspace. Never 0: that is the install's.
THROWAWAY_REDIS_DB = 1


def log(message: str) -> None:
    print(f"[throwaway] {message}", file=sys.stderr, flush=True)


def fail(message: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"[throwaway] ✗ {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ── what we borrow ────────────────────────────────────────────────────────────


def released_runtime() -> Path:
    """The newest installed runtime release, for its guest and its host pack."""
    releases = INSTALL_ROOT / "runtime" / "releases"
    candidates = sorted(p for p in releases.glob("*") if p.is_dir())
    if not candidates:
        fail(
            f"no installed runtime under {releases}. Install Lemma Desktop and "
            "let it finish its first run; this borrows its guest image rather "
            "than downloading half a gigabyte again."
        )
    return candidates[-1]


def guest_infrastructure() -> dict:
    """Where the install's guest keeps its infrastructure, and how to open it.

    Credentials come from the install's own ``infra.secrets.json`` -- this
    checkout's locald mints *different* ones for a fresh root, and using those
    against a borrowed server fails authentication. Addresses are the guest's
    real ones: the rendered manifest names loopback forwards, which is not what
    a process outside the install can reach.
    """
    secrets_path = INSTALL_ROOT / "locald" / "infra.secrets.json"
    if not secrets_path.is_file():
        fail(f"{secrets_path} is missing — start Lemma Desktop once first")
    secrets = json.loads(secrets_path.read_text())
    for key in ("postgres_password", "redis_password"):
        if not secrets.get(key):
            fail(f"{secrets_path} has no {key}")

    host = _guest_address()
    return {
        "host": host,
        "postgres_password": secrets["postgres_password"],
        "redis_password": secrets["redis_password"],
        "supertokens_url": _supertokens_url(host),
    }


def _supertokens_url(host: str) -> str:
    """The core that answers, not the port the manifest happens to name.

    Probed rather than assumed: the manifest records a loopback forward, and
    the published port can move between runs. SuperTokens answers `/hello`
    with `Hello`, which is a cheap and unambiguous fingerprint.
    """
    for port in (3567, 53567):
        try:
            with urllib.request.urlopen(
                f"http://{host}:{port}/hello", timeout=3
            ) as answer:
                if answer.status == 200 and b"Hello" in answer.read(64):
                    return f"http://{host}:{port}"
        except (urllib.error.URLError, OSError):
            continue
    fail(
        f"no SuperTokens core answered on {host}. Is the install past first-run setup?"
    )


def _is_guest_address(address):
    """Is this the private address family the VZ guest lives on?

    Loopback is explicitly not the guest: the install's backend reaches the VM
    over its private network, and anything on 127.0.0.1 is something else on
    this Mac.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_private and not parsed.is_loopback


def _guest_address() -> str:
    """Ask the OS which guest the install's backend is actually talking to.

    Discovered from live sockets rather than read from the manifest: the
    manifest names loopback forwards that only exist inside the install, and
    the guest's own address is what a process outside it can open.
    """
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        fail(f"could not inspect sockets to find the guest: {error}")
    import re

    # Must be the VZ guest, not merely something answering on those ports.
    #
    # `lsof` is machine-wide, and this repo's own docker-compose publishes
    # Postgres on 5432, Redis on 6379 and SuperTokens on 3567 to 127.0.0.1. A
    # developer with the dev stack up would otherwise have loopback discovered
    # as "the guest", and the harness would then CREATE DATABASE and run the
    # full migration set against the wrong server -- non-deterministically,
    # since it depends on which connection `lsof` lists first.
    for match in re.finditer(r"->(\d+\.\d+\.\d+\.\d+):(?:5432|6379)\b", out):
        address = match.group(1)
        if _is_guest_address(address):
            return address
    fail(
        "no connection to a guest postgres/redis was found. Is Lemma Desktop "
        "running and past its first-run setup?"
    )


# ── staging ───────────────────────────────────────────────────────────────────


def stage_host_pack(root: Path, release: Path) -> Path:
    """A released host pack, with this checkout's backend laid over it.

    Cloned with APFS `cp -c`, so a gigabyte costs a few seconds and no disk.
    Overlaying rather than rebuilding keeps every part that is not being tested
    byte-identical to what shipped, and takes seconds instead of half an hour.
    """
    pack = root / "local-runtime"
    if pack.exists():
        shutil.rmtree(pack)
    log("cloning the released host pack…")
    clone = subprocess.run(
        ["cp", "-Rc", str(release / "local-runtime"), str(pack)],
        capture_output=True,
        text=True,
    )
    if clone.returncode != 0:  # not APFS, or cross-volume
        log("clonefile unavailable, copying (slower, uses disk)…")
        shutil.copytree(release / "local-runtime", pack)

    site_packages = list(pack.glob("backend/python/lib/python*/site-packages"))
    if not site_packages:
        fail(f"no site-packages inside the staged pack at {pack}")
    target = site_packages[0] / "app"
    source = REPO_ROOT / "lemma-backend" / "app"
    if not source.is_dir():
        fail(f"no backend source at {source}")
    log("overlaying this checkout's backend…")
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "test_*"),
    )
    return pack


def render_environment(root: Path, pack: Path, release: Path) -> dict:
    """Run this checkout's locald far enough to render its own manifest.

    `Daemon::new` does the rendering, and `serve()` is what would go on to start
    anything -- so the daemon is started, watched for `host-pack.json`, and
    stopped. Nothing boots.

    This is the whole reason the harness is trustworthy: the cookie domain under
    test is read out of what locald actually produced.
    """
    # Built here rather than assumed. `cargo test` builds a test harness and
    # leaves this binary untouched, so a run right after one silently renders
    # its environment with whatever locald was compiled last -- which is how a
    # change gets "verified" against a stale artifact that does not contain it.
    log("building locald from this checkout…")
    build = subprocess.run(
        ["cargo", "build", "--locked", "-p", "lemma-locald"],
        cwd=str(REPO_ROOT / "desktop"),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if build.returncode != 0:
        fail(f"could not build locald:\n{build.stderr[-3000:]}")

    locald = REPO_ROOT / "desktop" / "target" / "debug" / "lemma-locald"
    if not locald.is_file():
        fail(f"{locald} is missing even after a successful build")
    vz = INSTALLED_APP / "Contents" / "Resources" / "lemma-vz"
    bridge = INSTALLED_APP / "Contents" / "MacOS" / "lemma-runtime"
    for path in (vz, bridge):
        if not path.is_file():
            fail(
                f"{path} is missing — this borrows the installed app's signed sidecars"
            )

    locald_root = root / "locald"
    locald_root.mkdir(parents=True, exist_ok=True)
    environment = {
        **os.environ,
        "LEMMA_LOCALD_ROOT": str(locald_root),
        "LEMMA_LOCALD_HOST_PACK_ROOT": str(pack),
        "LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT": str(release / "managed-runtime"),
        "LEMMA_LOCALD_VZ_BIN": str(vz),
        "LEMMA_LOCALD_RUNTIME_BRIDGE_BIN": str(bridge),
    }
    manifest_path = locald_root / "host-pack.json"
    manifest_path.unlink(missing_ok=True)

    log("rendering the environment with this checkout's locald…")
    with (root / "locald-render.log").open("w") as output:
        daemon = subprocess.Popen(
            [str(locald), "serve"],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        for _ in range(120):
            if manifest_path.is_file():
                break
            if daemon.poll() is not None:
                fail(
                    "locald exited before rendering a manifest:\n"
                    + (root / "locald-render.log").read_text()[-2000:]
                )
            time.sleep(0.25)
        else:
            fail("locald never rendered host-pack.json")
    finally:
        _terminate(daemon)
    return json.loads(manifest_path.read_text())


def _terminate(process: subprocess.Popen) -> None:
    """Stop a child and everything it started, then confirm it is gone.

    By process group and by handle -- never by name. A `pkill lemma-locald`
    here would take out the developer's real daemon, which is the kind of
    "cleanup" that costs somebody an afternoon.
    """
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=10)


# ── databases ─────────────────────────────────────────────────────────────────


def psql(host: str, password: str, database: str, sql: str) -> str:
    result = subprocess.run(
        ["psql", "-h", host, "-U", "postgres", "-d", database, "-Atc", sql],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PGPASSWORD": password},
    )
    if result.returncode != 0:
        fail(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def make_databases(host: str, password: str, suffix: str) -> tuple[str, str]:
    """Throwaway databases beside the install's, never inside them."""
    app_db = f"lemma_e2e_{suffix}"
    data_db = f"lemma_datastore_e2e_{suffix}"
    for name in (app_db, data_db):
        psql(host, password, "postgres", f'DROP DATABASE IF EXISTS "{name}"')
        psql(host, password, "postgres", f'CREATE DATABASE "{name}"')
    log(f"created {app_db} and {data_db}")
    return app_db, data_db


def drop_databases(host: str, password: str, suffix: str) -> None:
    for name in (f"lemma_e2e_{suffix}", f"lemma_datastore_e2e_{suffix}"):
        # Refuse anything that is not ours, however this was called.
        if not name.startswith(("lemma_e2e_", "lemma_datastore_e2e_")):
            fail(f"refusing to drop {name}")
        psql(host, password, "postgres", f'DROP DATABASE IF EXISTS "{name}"')
    log("dropped the throwaway databases")


# ── the backend ───────────────────────────────────────────────────────────────


def backend_environment(
    manifest: dict, root: Path, infra: dict, app_db: str, data_db: str
) -> dict:
    """locald's own environment, with only borrowed infrastructure redirected.

    Everything this checkout's locald decided is kept exactly as rendered --
    ports, ``SESSION_COOKIE_DOMAIN``, ``APP_BASE_DOMAIN``, the gateway URLs.
    Only four things change, and each is a place the throwaway stack must not
    share with the install: the two databases, Redis, and the storage roots.

    Rebuilt from the install's credentials rather than string-patched from
    locald's: a fresh root mints fresh infra secrets, and those do not open a
    server that was initialised with the install's.
    """
    env = dict(manifest["services"][0]["env"])
    host = infra["host"]

    postgres = f"postgresql+asyncpg://postgres:{infra['postgres_password']}@{host}:5432"
    env["DATABASE_URL"] = f"{postgres}/{app_db}"
    env["DATASTORE_DATABASE_URL"] = f"{postgres}/{data_db}"
    # Database 1, not 0. Redis is borrowed, and the install's backend is a live
    # consumer of every stream in db 0 -- two backends in the same keyspace join
    # each other's consumer groups and steal each other's events, which would
    # make this harness break the very install it is borrowing from. A numbered
    # database is a full keyspace of its own, streams included.
    env["REDIS_URL"] = (
        f"redis://:{infra['redis_password']}@{host}:6379/{THROWAWAY_REDIS_DB}"
    )
    env["SUPERTOKENS_CORE_URL"] = infra["supertokens_url"]

    # Any other name for the same things, so a key added later cannot quietly
    # point this stack back at the install's data.
    for key, value in list(env.items()):
        if not isinstance(value, str):
            continue
        if "127.0.0.1:55432" in value:
            env[key] = value.replace("127.0.0.1:55432", f"{host}:5432")
        elif "127.0.0.1:56379" in value:
            env[key] = value.replace(
                "127.0.0.1:56379", f"{host}:6379/{THROWAWAY_REDIS_DB}"
            )

    # The backend locald spawns is deliberately tied to locald's lifetime: the
    # watchdog holds the inherited stdin pipe and exits the process the moment
    # it closes, so a daemon that dies cannot leave an orphaned backend behind.
    # There is no owning locald here -- it was stopped once it had rendered this
    # environment -- so the pipe is closed from the start and the backend exits
    # 0 immediately, silently, before it serves anything.
    #
    # Opted out rather than faked, because the harness owns that duty instead:
    # it records the pid and `down` stops it by process group.
    env.pop("LEMMA_LOCALD_PARENT_WATCHDOG", None)

    storage = root / "storage"
    (storage / "files").mkdir(parents=True, exist_ok=True)
    (storage / "objects").mkdir(parents=True, exist_ok=True)
    env["LOCAL_FILE_STORAGE_ROOT"] = str(storage / "files")
    env["LOCAL_OBJECT_STORAGE_ROOT"] = str(storage / "objects")
    return env


def run_migrations(pack: Path, env: dict, root: Path) -> None:
    python = next(pack.glob("backend/python/bin/python3"))
    log("running migrations into the throwaway database…")
    result = subprocess.run(
        [str(python), "-m", "alembic", "upgrade", "head"],
        cwd=str(pack / "backend"),
        env={**env, "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        timeout=900,
    )
    (root / "migrations.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        fail(
            f"migrations failed; see {root / 'migrations.log'}\n{result.stderr[-2000:]}"
        )


def start_backend(pack: Path, env: dict, root: Path, port: int) -> subprocess.Popen:
    python = next(pack.glob("backend/python/bin/python3"))
    log(f"starting the backend on {port}…")
    output = (root / "backend.log").open("w")
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "uvicorn",
            "local_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ws",
            "websockets-sansio",
        ],
        cwd=str(pack / "backend"),
        env={**env, "PATH": os.environ.get("PATH", "")},
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (root / "backend.pid").write_text(str(process.pid))
    return process


def wait_ready(
    url: str, process: subprocess.Popen, root: Path, seconds: int = 180
) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            fail(
                "the backend exited during start-up:\n"
                + (root / "backend.log").read_text()[-3000:]
            )
        try:
            with urllib.request.urlopen(f"{url}/health/ready", timeout=5) as answer:
                if answer.status == 200:
                    log("backend is ready")
                    return
        except (urllib.error.URLError, OSError):
            # Not yet listening, or listening and still starting up. That is the
            # normal state for most of this loop, so it is waited out rather
            # than reported; the deadline below is what turns it into a failure.
            pass
        time.sleep(1)
    fail(
        f"the backend never became ready:\n{(root / 'backend.log').read_text()[-3000:]}"
    )


# ── commands ──────────────────────────────────────────────────────────────────


def command_up(root: Path) -> None:
    """Stand the stack up, and leave nothing behind if it cannot be stood up.

    ``stack.json`` is written last and ``down`` is keyed on it -- so a failure
    before that point used to leave two databases inside the developer's real
    install's Postgres with nothing that would ever look for them again. A
    migration failure is the *likely* outcome in a lane whose whole purpose is
    running uncommitted backend code, and Ctrl-C raises ``KeyboardInterrupt``,
    which no ``except SystemExit`` catches.
    """
    created = {"root": root, "suffix": None, "host": None, "process": None}
    try:
        _command_up(root, created)
    except BaseException:
        _abandon(created)
        raise


def _abandon(created):
    """Undo a half-built stack, quietly and completely."""
    if created["process"] is not None:
        _terminate(created["process"])
    if created["suffix"] and created["host"]:
        try:
            secrets = json.loads(
                (INSTALL_ROOT / "locald" / "infra.secrets.json").read_text()
            )
            drop_databases(
                created["host"], secrets["postgres_password"], created["suffix"]
            )
        except (OSError, json.JSONDecodeError, KeyError, SystemExit) as error:
            log("could not drop the throwaway databases: {}".format(error))
    try:
        _remove_throwaway_root(created["root"])
    except SystemExit:
        # The guard refused the path, which means there is nothing here this
        # code is entitled to delete. Swallowed on purpose: this runs while
        # another failure is already propagating, and replacing that failure
        # with this one would hide the reason the stack could not start.
        pass


def _command_up(root: Path, created: dict) -> None:
    if (root / "stack.json").is_file():
        fail(f"{root} is already up — run `down` first")
    root.mkdir(parents=True, exist_ok=True)
    suffix = str(os.getpid())

    release = released_runtime()
    infra = guest_infrastructure()
    host, password = infra["host"], infra["postgres_password"]
    log(f"borrowing the guest at {host}")

    pack = stage_host_pack(root, release)
    manifest = render_environment(root, pack, release)
    env = manifest["services"][0]["env"]
    port = int(env["API_URL"].rsplit(":", 1)[-1])
    app_base = env["APP_BASE_DOMAIN"]

    created["host"] = host
    created["suffix"] = suffix
    app_db, data_db = make_databases(host, password, suffix)
    backend_env = backend_environment(manifest, root, infra, app_db, data_db)
    run_migrations(pack, backend_env, root)
    process = start_backend(pack, backend_env, root, port)
    created["process"] = process

    api_url = f"http://app.lemma.localhost:{port}"
    wait_ready(api_url, process, root)

    stack = {
        "root": str(root),
        "api_url": api_url,
        # No frontend is started: the probe signs in from the API's own origin,
        # which is the same host on another port, and cookies ignore ports.
        "frontend_url": api_url,
        "app_base_domain": app_base,
        "session_cookie_domain": env.get("SESSION_COOKIE_DOMAIN"),
        # Functions are dispatched into guest sandboxes by locald's runtime
        # bridge, and this stack has no locald -- it borrowed one long enough to
        # render an environment and stopped it. Recorded rather than left to be
        # discovered as a two-minute timeout.
        "provisions_sandboxes": False,
        "backend_pid": process.pid,
        "guest_host": host,
        "suffix": suffix,
    }
    (root / "stack.json").write_text(json.dumps(stack, indent=2))
    log("up")
    print(json.dumps(stack, indent=2))


def command_down(root: Path) -> None:
    stack_file = root / "stack.json"
    if not stack_file.is_file():
        log(f"nothing recorded under {root}; nothing to do")
        return
    stack = json.loads(stack_file.read_text())

    pid = stack.get("backend_pid")
    if pid:
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
            log(f"stopped the backend ({pid})")
        except (ProcessLookupError, PermissionError, OSError):
            log(f"backend {pid} was already gone")
    time.sleep(1)

    # Only what dropping actually needs. Teardown used to go through the full
    # discovery, which also probes for a SuperTokens core -- so one transient
    # failure there meant the databases were abandoned, and they accumulate
    # silently because nothing ever looks for them again.
    try:
        secrets = json.loads(
            (INSTALL_ROOT / "locald" / "infra.secrets.json").read_text()
        )
        drop_databases(
            stack["guest_host"], secrets["postgres_password"], stack["suffix"]
        )
    except (OSError, json.JSONDecodeError, KeyError, SystemExit) as error:
        log(
            f"could not drop the throwaway databases ({error}). They are named "
            f"lemma_e2e_{stack['suffix']} and lemma_datastore_e2e_{stack['suffix']}; "
            "drop them by hand or re-run `down` once the guest is back."
        )

    _remove_throwaway_root(root)


def _remove_throwaway_root(root: Path) -> None:
    """Delete the throwaway root, and refuse anything that is not one.

    Both conditions, not either: under the system temp directory *and* named
    for this harness. The first spelling of this guard was `not under /tmp and
    not named ...`, which refuses only when both fail -- so it would have
    removed any path under /tmp, or any directory whose name merely contained
    the marker. It also compared against "/tmp" while macOS resolves that to
    "/private/tmp", so the check that actually held was the loose one.

    Paths are compared after `resolve()` so a symlink cannot point this
    somewhere else.
    """
    resolved = root.resolve()
    # Both spellings of "temporary" on macOS: `gettempdir()` follows $TMPDIR to
    # a per-user folder, while /tmp is the conventional one this defaults to and
    # resolves to /private/tmp. Checking only the first refuses this harness's
    # own root.
    temporary = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    inside_temp = any(resolved.is_relative_to(candidate) for candidate in temporary)
    named_for_us = resolved.name.startswith(DEFAULT_ROOT.name)
    if not (inside_temp and named_for_us):
        fail(
            f"refusing to remove {resolved}: a throwaway root has to be under "
            f"one of {sorted(str(t) for t in temporary)} and named "
            f"{DEFAULT_ROOT.name}*"
        )
    shutil.rmtree(resolved, ignore_errors=True)
    if resolved.exists():
        log(f"warning: {resolved} could not be fully removed")
    else:
        log(f"removed {resolved}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "down"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    arguments = parser.parse_args()
    if sys.platform != "darwin":
        fail("the desktop build and its guest are macOS-only")
    if arguments.command == "up":
        command_up(arguments.root)
    else:
        command_down(arguments.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
