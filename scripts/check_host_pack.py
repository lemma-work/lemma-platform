#!/usr/bin/env python3
"""Prove a built host pack is one the desktop app can actually start.

The pack is ~700 MB of relocated interpreter and standalone server that nothing
in CI had ever produced, let alone run. Every check that did exist was about
files being present, and presence is the one thing that is never the problem: a
relocatable CPython whose baked `sys.prefix` points at the build machine unpacks
perfectly, reports the right version, and fails the instant it is asked to
import anything -- on a user's machine, four minutes into a first run, behind a
progress bar.

So this executes what the pack ships rather than looking at it.

    python3 scripts/check_host_pack.py <pack-root>

Paths come from `desktop/contracts/host-pack-layout.json`, the same file
`native_host_pack.rs` and `build_local_host_pack.py` are pinned against, so this
cannot drift into checking a layout nobody produces.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (REPO_ROOT / "desktop" / "contracts" / "host-pack-layout.json").read_text()
)


def resolve(pack: Path, what: str) -> Path:
    """The first candidate that exists, or a failure naming all of them."""
    entry = next(item for item in CONTRACT["required"] if item["what"] == what)
    for candidate in entry["candidates"]:
        path = pack / candidate
        if path.is_file():
            return path
    raise SystemExit(
        f"{what} is missing from the pack. Looked for: "
        + ", ".join(entry["candidates"])
    )


def check_output(*command: str | Path, cwd: Path | None = None) -> str:
    try:
        return subprocess.run(
            [str(part) for part in command],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"{command[0]} failed with status {error.returncode}\n"
            f"  command: {' '.join(str(part) for part in command)}\n"
            f"  stdout: {error.stdout.strip()}\n"
            f"  stderr: {error.stderr.strip()}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise SystemExit(f"{command[0]} did not answer within 180s") from error


def check_python(pack: Path) -> None:
    python = resolve(pack, "backend Python")

    # `sys.prefix` is the whole point. A relocatable build that did not get
    # relocated reports the build machine's path here, and every import after
    # it resolves against a directory that does not exist on this computer.
    prefix = Path(check_output(python, "-c", "import sys; print(sys.prefix)"))
    if not prefix.is_relative_to(pack.resolve()):
        raise SystemExit(
            f"the packed Python thinks it lives at {prefix}, which is outside "
            f"the pack at {pack}. It was not relocated, so nothing it imports "
            f"will resolve on a machine that is not the build machine."
        )

    # The standard library, then the app. Both, because a pack can carry a
    # working interpreter and no dependencies, or dependencies and a broken
    # ssl module -- and the second is the one that fails at the first HTTPS
    # call rather than at startup.
    check_output(python, "-c", "import ssl, sqlite3, zlib, lzma, ctypes")
    version = check_output(
        python, "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"
    )
    if version != "3.14":
        raise SystemExit(f"the packed Python is {version}; the backend needs 3.14")
    print(f"  python   {version} at {python.relative_to(pack)}, prefix inside the pack")


def check_node(pack: Path) -> None:
    node = resolve(pack, "frontend Node.js")
    launcher = resolve(pack, "frontend launcher")
    server = resolve(pack, "Next.js standalone server")

    version = check_output(node, "--version")
    if not version.startswith("v2"):
        raise SystemExit(f"the packed Node is {version}, which no Next build targets")

    # Parsing is not running, but it is the half that catches a truncated copy
    # or a standalone build emitted for a different Node major -- and running
    # the real server would need a database behind it.
    check_output(node, "--check", server)
    check_output(node, "--check", launcher)

    # The launcher is the one piece here that can be run to completion, because
    # its first act is to refuse. Worth doing: it is what locald spawns, so a
    # launcher that cannot start under the *packed* Node -- a syntax level the
    # copied binary does not support, a missing ESM flag -- is a frontend that
    # never serves, and "it parses" would not have caught it.
    refusal = subprocess.run(
        [str(node), str(launcher)], capture_output=True, text=True, timeout=60
    )
    if refusal.returncode == 0:
        raise SystemExit(
            "the frontend launcher started with no server path. It is spawned "
            "with one; starting without one means it would silently serve "
            "nothing rather than say so."
        )
    if "Next.js server path" not in refusal.stderr:
        raise SystemExit(
            f"the frontend launcher failed for some reason other than its "
            f"missing argument, which means it did not get as far as checking:\n"
            f"{refusal.stderr.strip()}"
        )
    print(f"  node     {version} at {node.relative_to(pack)}, server parses, launcher runs")


def check_derived(pack: Path) -> None:
    """Present, and not empty where emptiness means broken.

    An existence check passes on a zero-byte `lemma-client.js`, an empty
    `migrations/` and a truncated `alembic.ini` -- each of which produces an
    install that gets further before it fails, which is worse than one that
    fails here.
    """
    problems = []
    for entry in CONTRACT["derived"]:
        path = pack / entry["path"]
        if not path.exists():
            problems.append(f"{entry['what']} is missing ({entry['path']})")
            continue
        if not entry.get("must_not_be_empty"):
            continue
        empty = (
            not any(path.iterdir()) if path.is_dir() else path.stat().st_size == 0
        )
        if empty:
            problems.append(f"{entry['what']} is empty ({entry['path']})")
    if problems:
        raise SystemExit("the pack is not usable: " + "; ".join(problems))
    print(f"  assets   {len(CONTRACT['derived'])} derived paths present and non-empty")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <pack-root>")
    pack = Path(sys.argv[1]).resolve()
    if not pack.is_dir():
        raise SystemExit(f"not a host pack directory: {pack}")

    print(f"Checking host pack at {pack}")
    check_python(pack)
    check_node(pack)
    check_derived(pack)
    print("Host pack is startable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
