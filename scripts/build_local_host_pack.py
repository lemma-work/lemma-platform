#!/usr/bin/env python3
"""Build a relocatable native backend + frontend pack for Lemma Desktop.

The result contains an Astral standalone Python installation with wheel-built
Lemma packages, an official Node distribution, the Next standalone output, and
the static assets required by locally served pod apps. Nothing resolves or
installs dependencies on the user's machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_WHEEL_PROJECTS = (
    "lemma-pod-bundle",
    "lemma-backend/lemma-connectors",
    "lemma-backend",
)
LOCAL_WHEEL_PACKAGES = (
    "lemma-pod-bundle",
    "lemma-connectors",
    "lemma-backend",
)


def run(*args: str | Path, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> None:
    command = [str(arg) for arg in args]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def run_with_retries(
    *args: str | Path,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    attempts: int = 3,
) -> None:
    """Run a command that can fail for reasons that have nothing to do with us.

    The Next.js build resolves `next/font/google` by fetching from
    fonts.googleapis.com at build time, so it is only as reliable as that
    request. It has failed five of our last seven runs -- on the arm64 image
    build, on the macOS host pack, on the Windows host pack -- always as a wall
    of "Error while requesting resource", always green on a re-run. Retrying
    here beats a human re-running the job.

    The real fix is to stop fetching fonts during a build at all: vendor the
    families and use `next/font/local`. That is a typography change across eight
    families and does not belong in a packaging script.
    """
    for attempt in range(1, attempts + 1):
        try:
            run(*args, cwd=cwd, env=env)
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            delay = 5 * attempt
            print(
                f"  command failed (attempt {attempt}/{attempts}); "
                f"retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)


def npm_executable(platform_name: str | None = None) -> str:
    platform_name = os.name if platform_name is None else platform_name
    return "npm.cmd" if platform_name == "nt" else "npm"


def one_child(directory: Path, label: str, *, preferred: str | None = None) -> Path:
    # uv also creates a version alias, lock metadata, and a .temp directory in
    # a managed Python install root. The relocatable distribution is the one
    # concrete, non-hidden directory; moving the alias would leave a dangling
    # absolute symlink after the staging root is removed.
    children = [
        path
        for path in directory.iterdir()
        if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
    ]
    preferred_children = [path for path in children if preferred and preferred in path.name]
    if len(preferred_children) == 1:
        return preferred_children[0]
    if len(children) != 1:
        raise SystemExit(f"expected exactly one {label} under {directory}, found {children}")
    return children[0]


def python_executable(python_root: Path) -> Path:
    candidates = (
        python_root / "python.exe",
        python_root / "bin/python3",
        python_root / "bin/python3.14",
        python_root / "bin/python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(f"standalone Python executable not found under {python_root}")


def install_python(
    output: Path,
    version: str,
    wheels: Path,
    explicit_python_root: Path | None = None,
) -> Path:
    destination = output / "backend/python"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if explicit_python_root is not None:
        source = explicit_python_root.resolve()
        executable = python_executable(source)
        detected = subprocess.check_output(
            [str(executable), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            text=True,
        ).strip()
        if detected != version:
            raise SystemExit(
                f"explicit Python root is {detected}, expected exact {version}: {source}"
            )
        shutil.copytree(source, destination, symlinks=True)
    else:
        staging = output / ".python-install"
        bin_dir = output / ".python-bin"
        environment = os.environ.copy()
        environment["UV_PYTHON_BIN_DIR"] = str(bin_dir)
        try:
            run(
                "uv",
                "python",
                "install",
                version,
                "--install-dir",
                staging,
                env=environment,
            )
        except subprocess.CalledProcessError as error:
            # The pinned interpreter is an exact patch version, and `uv` only
            # knows the builds its own release shipped with. An older `uv` --
            # which is what a laptop has and what a CI runner never has, since
            # setup-uv installs the latest -- reports "No download found for
            # request" and this raised a traceback ending in `subprocess.run`,
            # naming nothing a person could act on.
            raise SystemExit(
                f"uv could not fetch CPython {version}.\n"
                f"  Command: {' '.join(str(part) for part in error.cmd)}\n"
                f"  This is almost always an out-of-date uv: an exact patch "
                f"version only downloads from a uv release that knows about it. "
                f"Run `uv self update`, or pass --python-root to copy an "
                f"interpreter you already have (`uv python list --all-versions` "
                f"shows them)."
            ) from error
        installed = one_child(
            staging,
            "standalone Python installation",
            preferred=f"cpython-{version}-",
        )
        installed.replace(destination)
        shutil.rmtree(staging)
        shutil.rmtree(bin_dir, ignore_errors=True)

    executable = python_executable(destination)
    if os.name != "nt" and not (destination / "bin/python3").exists():
        (destination / "bin/python3").symlink_to(executable.name)
        executable = destination / "bin/python3"

    wheel_files: list[Path] = []
    for project in LOCAL_WHEEL_PROJECTS:
        run("uv", "build", "--wheel", "--out-dir", wheels, REPO_ROOT / project)
    for project in LOCAL_WHEEL_PROJECTS:
        normalized_name = Path(project).name.replace("-", "_")
        matches = sorted(wheels.glob(f"{normalized_name}-*.whl"))
        if len(matches) != 1:
            raise SystemExit(f"expected one wheel for {project}, found {matches}")
        wheel_files.append(matches[0])

    # Install the registry dependency graph exactly as tested in uv.lock. A
    # plain `uv pip install lemma-backend` re-resolves ranges and can silently
    # produce an incompatible pack even when the repository lock is healthy.
    locked_requirements = wheels / "locked-requirements.txt"
    run(
        "uv",
        "export",
        "--project",
        REPO_ROOT / "lemma-backend",
        "--frozen",
        "--no-dev",
        "--extra",
        "local",
        "--extra",
        "keychain",
        "--no-emit-project",
        *(
            argument
            for package in LOCAL_WHEEL_PACKAGES
            for argument in ("--no-emit-package", package)
        ),
        "--no-hashes",
        "--quiet",
        "--output-file",
        locked_requirements,
    )
    run(
        "uv",
        "pip",
        "install",
        "--python",
        executable,
        # This is a private copied standalone distribution, not the user's
        # system Python. uv marks managed downloads as externally managed, so
        # explicitly authorize installing the application into this pack.
        "--break-system-packages",
        "--find-links",
        wheels,
        "--requirements",
        locked_requirements,
        *wheel_files,
    )
    prune_python_runtime(destination)
    compile_python_runtime(destination, executable)
    smoke_environment = os.environ.copy()
    smoke_environment.update(
        {
            "WORKSPACE_PROVIDER": "lemma_local",
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY": "host-pack-smoke-runtime-key-000000",
            # Provider construction validates that the managed-runtime bridge
            # exists. The private Python executable is a harmless stand-in for
            # this build-only import smoke test; no bridge request is made.
            "WORKSPACE_LOCAL_RUNTIME_CLI": str(executable),
        }
    )
    run(
        executable,
        "-c",
        (
            "from fastmcp import FastMCP; "
            "from app.modules.workspace.providers.lemma_local import "
            "LemmaLocalSandboxProvider; "
            "import keyring, local_app, uvicorn, xberg; "
            "assert LemmaLocalSandboxProvider.name == 'lemma_local'; "
            "print('backend pack: import ok')"
        ),
        env=smoke_environment,
    )
    return executable


def prune_python_runtime(python_root: Path) -> None:
    """Remove build/test-only files while retaining package metadata and data."""

    # Bytecode is cleared here and rebuilt by `compile_python_runtime` once the
    # pruning below has finished, so nothing is compiled that is about to be
    # deleted and every `.pyc` in the pack was written with the same
    # invalidation mode. Leaving this out and keeping whatever uv or the
    # interpreter distribution happened to ship would mix modes silently.
    for cache in sorted(python_root.rglob("__pycache__"), reverse=True):
        shutil.rmtree(cache, ignore_errors=True)
    for compiled in python_root.rglob("*.py[co]"):
        compiled.unlink(missing_ok=True)

    for relative in ("include", "share/man", "share/doc"):
        shutil.rmtree(python_root / relative, ignore_errors=True)

    for standard_library in python_root.glob("lib/python*"):
        for name in ("test", "idlelib", "turtledemo", "ensurepip"):
            shutil.rmtree(standard_library / name, ignore_errors=True)
        site_packages = standard_library / "site-packages"
        for package in ("app", "lemma_connectors"):
            root = site_packages / package
            if not root.is_dir():
                continue
            for tests in sorted(root.rglob("tests"), reverse=True):
                shutil.rmtree(tests, ignore_errors=True)


def compile_python_runtime(python_root: Path, executable: Path) -> None:
    """Precompile the pack's bytecode, so the first launch does not.

    Without this the pack ships pure source: `prune_python_runtime` above
    removes the 418 stdlib `.pyc` the standalone CPython distribution ships,
    `UV_COMPILE_BYTECODE` is not set for the install, and nothing else
    compiles the application's ~1,300 modules. The install root is writable, so
    Python caches on first use -- which means the entire parse-and-compile is
    paid on the first backend start after every install and every update.

    Measured on an M-series Mac against the shipped 0.7.0 runtime, `import
    app.app` alone: 7.62s with no cached bytecode, 2.81s with it. That is ~4.8s
    on the launch a person forms their first impression from, and it is the
    same defect the container build already fixed (see the note beside
    `compileall` in `lemma-backend/Dockerfile`).

    `unchecked-hash` rather than the default timestamp mode for the same reason
    the container uses it: a timestamp-invalidated `.pyc` makes the interpreter
    `stat` every source file on every start to decide whether the cache is
    stale. Nothing rewrites a `.py` inside an installed pack, so that check
    buys nothing and costs a syscall per module forever.

    Failures are reported and not fatal. A pack that ships a module some
    dependency cannot compile -- vendored Python 2, a template with
    placeholders -- is a pack that works today, because the file is never
    imported. Refusing to build the DMG over one would trade a real release for
    an optimisation.
    """
    command = [
        str(executable),
        "-m",
        "compileall",
        "-q",
        # One worker per core: this walks ~18k files and is pure CPU.
        "-j",
        "0",
        "--invalidation-mode",
        "unchecked-hash",
        str(python_root),
    ]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    compiled = sum(1 for _ in python_root.rglob("*.pyc"))
    if completed.returncode != 0:
        print(
            f"  warning: compileall reported errors (exit {completed.returncode}); "
            f"{compiled} modules were still compiled. The pack is usable -- the "
            "uncompiled ones cost parse time only if something imports them.",
            flush=True,
        )
    if compiled == 0:
        raise SystemExit(
            "compileall wrote no bytecode; the pack would ship pure source and "
            "pay the full compile on a user's first launch"
        )
    print(f"  compiled {compiled} modules", flush=True)


def copy_browser_assets(output: Path) -> None:
    browser = output / "backend/assets/browser-sdk"
    browser.mkdir(parents=True, exist_ok=True)
    for name in ("lemma-client.js", "lemma-ui.js"):
        source = REPO_ROOT / f"lemma-typescript/public/{name}"
        if not source.is_file():
            raise SystemExit(f"browser bundle is missing: {source}")
        shutil.copy2(source, browser / name)


def copy_backend_assets(output: Path) -> None:
    backend = output / "backend"
    copy_browser_assets(output)
    shutil.copytree(REPO_ROOT / "lemma-skills", backend / "assets/lemma-skills")
    shutil.copytree(REPO_ROOT / "lemma-backend/migrations", backend / "migrations")
    shutil.copy2(REPO_ROOT / "lemma-backend/alembic.ini", backend / "alembic.ini")
    copy_catalog_importer(backend)


def copy_catalog_importer(backend: Path) -> None:
    """The connector catalog seeder, which locald runs beside the migrations.

    Without this a packaged install has no connector catalog at all: `make dev`
    seeds one, and nothing in the shipped app ever did, so every connector was
    missing on a machine that had only ever run the installer.

    The importer is a script rather than part of the backend package, so it is
    copied next to the migrations it runs after — and its native app definitions
    come with it, since that JSON *is* the catalog for everything that does not
    come from Composio.
    """
    scripts = backend / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    source = REPO_ROOT / "lemma-backend/scripts"
    for name in ("import_connector_catalog.py", "lemma_apps_config.json"):
        shutil.copy2(source / name, scripts / name)


def node_root(explicit: Path | None) -> Path:
    executable = Path(
        subprocess.check_output(
            ["node", "-p", "process.execPath"], text=True, cwd=REPO_ROOT
        ).strip()
    ).resolve()
    root = explicit.resolve() if explicit else (
        executable.parent if os.name == "nt" else executable.parent.parent
    )
    expected = root / ("node.exe" if os.name == "nt" else "bin/node")
    if not expected.is_file():
        raise SystemExit(f"Node root {root} does not contain {expected.relative_to(root)}")
    if root == Path(root.anchor) or len(root.parts) < 3:
        raise SystemExit(f"refusing unsafe Node root: {root}")
    return root


def node_rpath_libraries(executable: Path, root: Path) -> list[Path]:
    """The dylibs inside the Node installation that its executable needs.

    The nodejs.org tarballs are a single static executable, and for a long time
    that was the whole story: copy `bin/node` and nothing else. It is not true
    of every build. The GitHub tool-cache build of Node 22.23.1 for macOS arm64
    links `@rpath/libnode.127.dylib`, and Homebrew's links a dozen of its own
    kegs. Copying the executable alone from either produces a pack that unpacks
    cleanly, passes every file-existence check, and dies with a dyld error the
    first time the app tries to serve a page.

    Only `@rpath`/`@loader_path` entries are followed, and only to files inside
    the Node root. A dylib in /usr/lib is part of the OS and is there on the
    user's machine too; a Homebrew keg outside the root is not relocatable and
    is a Node that should not be packed at all, which `check_host_pack.py`
    then says out loud.
    """
    if sys.platform != "darwin":
        return []
    try:
        listing = subprocess.run(
            ["otool", "-L", str(executable)], text=True, capture_output=True, check=True
        ).stdout
    # Parenthesised on purpose. Unlike the backend, which pins 3.14, this
    # script is run by CI as a bare `python` -- so it must parse on whatever
    # interpreter the runner happens to have. PEP 758's unparenthesised form
    # is a SyntaxError before 3.14, and it took the Windows host pack with it.
    except (OSError, subprocess.CalledProcessError):
        # Not a Mach-O binary, or no Xcode command line tools. Either way the
        # probe below is the one that decides whether this pack is usable.
        return []
    names = [line.strip().split(" (", 1)[0] for line in listing.splitlines()[1:]]
    return _resolve_rpath_libraries(root, executable, names)


def _resolve_rpath_libraries(
    root: Path, executable: Path, names: list[str]
) -> list[Path]:
    """The `otool -L` names that resolve to a file inside the Node root."""
    libraries = []
    for name in names:
        if not name.startswith(("@rpath/", "@loader_path/")):
            continue
        leaf = name.split("/", 1)[1]
        for candidate in (root / "lib" / leaf, executable.parent / leaf):
            if candidate.is_file():
                libraries.append(candidate)
                break
    return libraries


def copy_node_runtime(frontend: Path, explicit_root: Path | None) -> None:
    root = node_root(explicit_root)
    relative = Path("node.exe") if os.name == "nt" else Path("bin/node")
    source = root / relative
    destination = frontend / "node" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The executable and the libraries it carries an @rpath to, and nothing
    # else. Copying the whole installation would also bundle npm and any
    # unrelated globally installed packages from the build runner.
    shutil.copy2(source, destination)
    for library in node_rpath_libraries(source, root):
        # `bin/node` resolves @rpath through `@loader_path/../lib`, so the
        # layout inside the pack has to be the layout it came from.
        packed = destination.parent.parent / "lib" / library.name
        packed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(library, packed)

    # Ask the copy the only question that matters, here rather than in a later
    # job: a Node that cannot start is a frontend that never serves, and every
    # cheaper check -- the file exists, it is executable, it is the right
    # version -- passes on one that cannot.
    try:
        probe = subprocess.run(
            [str(destination), "--version"], text=True, capture_output=True
        )
        failure = (
            None
            if probe.returncode == 0
            else f"exited {probe.returncode}\n{probe.stderr.strip()}"
        )
    except OSError as error:
        failure = f"could not be started at all: {error}"
    if failure is not None:
        raise SystemExit(
            f"the packed Node cannot start: {destination} {failure}\n"
            f"It was copied from {source}. A Node that links libraries outside "
            f"its own installation cannot be packed; use a distribution from "
            f"nodejs.org (which `.nvmrc` and actions/setup-node give you)."
        )


def standalone_server(root: Path) -> Path:
    candidates = (
        root / "server.js",
        root / "app/server.js",
        root / "lemma-frontend/server.js",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "Next standalone server is missing; expected one of: "
        + ", ".join(str(path) for path in candidates)
    )


def build_frontend(output: Path, explicit_node_root: Path | None) -> None:
    npm = npm_executable()
    run(npm, "ci", cwd=REPO_ROOT / "lemma-typescript")
    run(npm, "run", "build", cwd=REPO_ROOT / "lemma-typescript")
    run(npm, "ci", cwd=REPO_ROOT / "lemma-frontend")
    run_with_retries(npm, "run", "build", cwd=REPO_ROOT / "lemma-frontend")

    frontend = output / "frontend"
    copy_node_runtime(frontend, explicit_node_root)
    standalone = REPO_ROOT / "lemma-frontend/.next/standalone"
    if not standalone.is_dir():
        raise SystemExit(f"Next standalone output is missing: {standalone}")
    server = standalone_server(standalone)
    shutil.copytree(standalone, frontend, dirs_exist_ok=True)
    server_dir = frontend / server.relative_to(standalone).parent
    shutil.copytree(
        REPO_ROOT / "lemma-frontend/public",
        server_dir / "public",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        REPO_ROOT / "lemma-frontend/.next/static",
        server_dir / ".next/static",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        REPO_ROOT / "desktop/runtime/frontend-launcher.mjs",
        frontend / "frontend-launcher.mjs",
    )


def archive_pack(output: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                archive.write(path, Path("local-runtime") / path.relative_to(output))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def size_breakdown(output: Path) -> dict[str, int]:
    frontend = output / "frontend"
    node_bytes = tree_size(frontend / "node")
    frontend_bytes = tree_size(frontend)
    python_bytes = tree_size(output / "backend/python")
    backend_bytes = tree_size(output / "backend")
    return {
        "python_bytes": python_bytes,
        "backend_assets_bytes": max(0, backend_bytes - python_bytes),
        "node_bytes": node_bytes,
        "next_bytes": max(0, frontend_bytes - node_bytes),
        "metadata_bytes": tree_size(output) - backend_bytes - frontend_bytes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--python", default="3.14.2")
    parser.add_argument(
        "--python-root",
        type=Path,
        help="copy an existing exact-version standalone Python distribution",
    )
    parser.add_argument("--node-root", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--archive-existing",
        action="store_true",
        help="archive an already-built output directory after platform signing",
    )
    parser.add_argument(
        "--refresh-frontend-existing",
        action="store_true",
        help="rebuild frontend and browser SDK assets in an existing host pack",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output.resolve()
    if args.archive_existing and args.refresh_frontend_existing:
        raise SystemExit(
            "--archive-existing and --refresh-frontend-existing are mutually exclusive"
        )
    if args.refresh_frontend_existing:
        if not args.archive:
            raise SystemExit("--refresh-frontend-existing requires --archive")
        metadata_path = output / "pack.json"
        if not metadata_path.is_file() or not (output / "release.json").is_file():
            raise SystemExit(f"existing host pack is incomplete: {output}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        shutil.rmtree(output / "frontend", ignore_errors=True)
        build_frontend(output, args.node_root)
        copy_browser_assets(output)
        archive_pack(output, args.archive)
        archive_metadata = {
            **metadata,
            "archive": str(args.archive),
            "sha256": sha256(args.archive),
            "size": args.archive.stat().st_size,
            "expanded_size": sum(
                path.stat().st_size for path in output.rglob("*") if path.is_file()
            ),
            "breakdown": size_breakdown(output),
        }
        args.archive.with_suffix(f"{args.archive.suffix}.json").write_text(
            json.dumps(archive_metadata, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(archive_metadata))
        return
    if args.archive_existing:
        if not args.archive:
            raise SystemExit("--archive-existing requires --archive")
        metadata_path = output / "pack.json"
        if not metadata_path.is_file() or not (output / "release.json").is_file():
            raise SystemExit(f"existing host pack is incomplete: {output}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        archive_pack(output, args.archive)
        archive_metadata = {
            **metadata,
            "archive": str(args.archive),
            "sha256": sha256(args.archive),
            "size": args.archive.stat().st_size,
            "expanded_size": sum(
                path.stat().st_size for path in output.rglob("*") if path.is_file()
            ),
            "breakdown": size_breakdown(output),
        }
        args.archive.with_suffix(f"{args.archive.suffix}.json").write_text(
            json.dumps(archive_metadata, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(archive_metadata))
        return
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    release_manifest = args.release_manifest.resolve()
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    images = release.get("images", {})
    workspace_image = images.get("workspace")
    function_image = images.get("function")
    if not release.get("version") or not workspace_image or not function_image:
        raise SystemExit(
            "release manifest lacks version, workspace, or function image"
        )

    with tempfile.TemporaryDirectory(prefix="lemma-host-wheels-") as wheel_dir:
        wheels = Path(wheel_dir)
        install_python(output, args.python, wheels, args.python_root)
    copy_backend_assets(output)
    build_frontend(output, args.node_root)
    shutil.copy2(release_manifest, output / "release.json")

    metadata = {
        "schema_version": 1,
        "release": str(release["version"]),
        "python": args.python,
        "platform": sys.platform,
        "architecture": os.uname().machine if hasattr(os, "uname") else os.environ.get(
            "PROCESSOR_ARCHITECTURE", "unknown"
        ),
    }
    (output / "pack.json").write_text(json.dumps(metadata, indent=2) + "\n")

    if args.archive:
        archive_pack(output, args.archive)
        archive_metadata = {
            **metadata,
            "archive": str(args.archive),
            "sha256": sha256(args.archive),
            "size": args.archive.stat().st_size,
            "expanded_size": sum(
                path.stat().st_size for path in output.rglob("*") if path.is_file()
            ),
            "breakdown": size_breakdown(output),
        }
        args.archive.with_suffix(f"{args.archive.suffix}.json").write_text(
            json.dumps(archive_metadata, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(archive_metadata))
    else:
        print(json.dumps(metadata))


if __name__ == "__main__":
    main()
