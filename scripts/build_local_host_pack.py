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
        run(
            "uv",
            "python",
            "install",
            version,
            "--install-dir",
            staging,
            env=environment,
        )
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
            "import keyring, local_app, markitdown, uvicorn; "
            "assert LemmaLocalSandboxProvider.name == 'lemma_local'; "
            "print('backend pack: import ok')"
        ),
        env=smoke_environment,
    )
    return executable


def prune_python_runtime(python_root: Path) -> None:
    """Remove build/test-only files while retaining package metadata and data."""

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


def copy_node_runtime(frontend: Path, explicit_root: Path | None) -> None:
    root = node_root(explicit_root)
    relative = Path("node.exe") if os.name == "nt" else Path("bin/node")
    source = root / relative
    destination = frontend / "node" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The official Node executable is self-contained on both target platforms.
    # Copying the whole installation would also bundle npm and any unrelated
    # globally installed packages from the build runner.
    shutil.copy2(source, destination)


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
    run(npm, "run", "build", cwd=REPO_ROOT / "lemma-frontend")

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
