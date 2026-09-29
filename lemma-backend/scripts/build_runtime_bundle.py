#!/usr/bin/env python3
"""The first-party code a workspace sandbox runs, as one content-addressed archive.

That is all of it: the SDK, the CLI and its skills, the workspace side of
``sandbox_runtime``, and the image's scripts. ``site-packages/`` for imports,
``bin/`` for commands and ``lib/`` for what the scripts read.

This exists so that shipping a Lemma code change stops requiring a new sandbox
template. On E2B the sandbox *is* the disk, and adopting a new template means
destroying the one that holds the user's files -- so every release that touched
``lemma-cli`` or ``lemma-python`` was a release that wiped workspaces. The
expensive parts of the image (Chromium, Node, the apt layer, pandas) change a
handful of times a year; the first-party payload changes on most releases, and
it is a couple of megabytes of Python and shell.

So it moves out of the image and into a bundle the backend installs into a
running sandbox. The template keeps a copy, which becomes a floor rather than
the shipped version: it is what supplies the third-party dependency closure, and
what a sandbox falls back to when an install fails.

**Two wheels, not three.** ``lemma-terminal`` vendors both ``lemma-skills`` and
``lemma_pod_bundle`` into itself at build time (see ``lemma-cli/setup.py``, and
``include = ["lemma_cli*", "lemma_pod_bundle*"]`` in its ``pyproject.toml``), so
building ``lemma-pod-bundle`` separately would put a second, competing copy of
the same top-level package into the same target directory.

**Determinism is the point, not a nicety.** The bundle's identity is a digest of
its contents, and that digest decides whether a sandbox needs reinstalling. Wheel
zips are not byte-reproducible, so the digest is taken over the *unpacked* file
contents -- sorted paths, each with its own sha256 -- which depends on nothing
but the sources. ``test_runtime_bundle.py`` builds twice and compares.

Usage::

    uv run python scripts/build_runtime_bundle.py --out-dir dist/runtime-bundle
"""

from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


#: Defaults for a checkout. Both are overridable because the backend image does
#: not reproduce the repository's shape: the first-party projects land at `/`
#: beside each other (which is what `lemma-cli/setup.py` needs, since it vendors
#: `../lemma-skills`), while the backend itself lives at `/app`. Deriving them
#: from `__file__` works in a checkout and silently points at nothing in a
#: container, which is the kind of difference that only shows up in production.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

#: The projects whose wheels carry every first-party package a sandbox imports.
#: Order is not significant -- nothing here may shadow anything else there.
WHEEL_PROJECTS = ("lemma-python", "lemma-cli")

#: Every part of ``sandbox_runtime`` a workspace sandbox runs, which is all of
#: it except the function runtime, the installer and the tests.
#:
#: All of it, not the part one fabric happens to need, because the overlay does
#: not add to the image's copy -- it replaces it. ``sandbox_runtime`` is a
#: regular package and the overlay is first on ``sys.path``, so Python finds the
#: package there and never looks in ``/app`` for a submodule the overlay lacks.
#: A bundle carrying three modules made every other one vanish the moment it
#: was installed: the relay's ``import sandbox_runtime.paths``, the Python
#: worker, and the workspace server of a container starting again.
#:
#: The image floor copies exactly this list too (``Dockerfile.workspace`` and
#: the E2B workspace template), so a sandbox with no overlay yet runs the same
#: modules as one with it. ``test_the_images_bake_the_overlay_floor`` holds the
#: three lists together.
RUNTIME_SOURCES = (
    "sandbox_runtime/__init__.py",
    "sandbox_runtime/contracts.py",
    "sandbox_runtime/errors.py",
    "sandbox_runtime/host_fallback.py",
    "sandbox_runtime/paths.py",
    "sandbox_runtime/protocol.py",
    "sandbox_runtime/sandbox_memory.py",
    "sandbox_runtime/tasks.py",
    "sandbox_runtime/browser_relay",
    "sandbox_runtime/workspace",
)

#: What stays out of the overlay, and why. The function runtime runs in its own
#: image, which has no overlay; the installer is uploaded on its own, before
#: any bundle exists; the tests and README are not code a sandbox runs.
RUNTIME_EXCLUDED = (
    "sandbox_runtime/README.md",
    "sandbox_runtime/function",
    "sandbox_runtime/runtime_install.py",
    "sandbox_runtime/tests",
)

#: Where the image's own scripts come from, and the names each is installed
#: under in the overlay's ``bin/``. The same names the images give the baked
#: copies in ``/usr/local/bin``, so the overlay's copy wins by coming first on
#: ``PATH`` and nothing that calls a script by name changes.
#:
#: ``lemma-node-tool`` is one script that dispatches on the name it was run
#: as, so it is installed once per name. The images symlink those names to one
#: file; an archive carries copies, which are a few kilobytes each.
SCRIPTS_DIRECTORY = "sandbox-images/scripts"
SCRIPT_NAMES: dict[str, tuple[str, ...]] = {
    "browser-is-live.sh": ("browser-is-live",),
    "lemma-ensure-display.sh": ("lemma-ensure-display",),
    "lemma-node-tool": ("agent-browser", "lit", "liteparse", "pnpm"),
    "lemma-uv": ("uv",),
    "save-webpage.sh": ("save-webpage",),
    "set-display-size.sh": ("set-display-size",),
    "start-browser-relay.sh": ("start-browser-relay",),
    "start-browser.sh": ("start-browser",),
    "start-vnc-bridge.sh": ("start-vnc-bridge",),
}

#: Files scripts read rather than run, installed into the overlay's ``lib/``.
SCRIPT_LIBRARIES = ("webpage-to-markdown.mjs",)

#: Scripts that stay baked only. Each is what a container runs as its command,
#: before the backend can have delivered any overlay, and the function
#: launcher belongs to an image with no overlay at all.
IMAGE_ONLY_SCRIPTS = ("start-workspace-runtime.sh", "lemma-function-runtime")

#: Everything the overlay must be able to import once installed. Recorded in the
#: manifest and checked by the installer *inside the sandbox*, which is the only
#: place the third-party closure exists -- the bundle is built ``--no-deps``, so
#: importing ``lemma_cli.cli`` here would only report which of ``typer``,
#: ``rich`` and ``textual`` happen to be in whatever environment ran the build.
#: A gate that answers a different question from the one it appears to ask is
#: worse than no gate, so this file checks structure and the installer checks
#: behaviour.
#:
#: The ``sandbox_runtime`` entries are the ones a sandbox process imports
#: first: the relay, the Python worker, the loopback fall-through and the
#: workspace runtime's app. ``workspace.app`` rather than ``workspace.server``,
#: because importing ``server`` builds the app, and that consumes the runtime's
#: single-use token.
REQUIRED_IMPORTS = (
    "lemma_sdk",
    "lemma_cli.cli",
    "lemma_pod_bundle",
    "sandbox_runtime.browser_relay",
    "sandbox_runtime.browser_relay.chrome",
    "sandbox_runtime.host_fallback",
    "sandbox_runtime.paths",
    "sandbox_runtime.workspace.app",
    "sandbox_runtime.workspace.python_worker",
)

#: What must be present in the staged tree, as directories. The skills entry is
#: the one that has actually shipped broken before: ``lemma-terminal`` 0.4.1 went
#: out with an empty package because the vendoring ran too late in the build.
REQUIRED_PACKAGES = (
    "lemma_sdk",
    "lemma_cli",
    "lemma_cli/skills",
    "lemma_pod_bundle",
    "sandbox_runtime/browser_relay",
    "sandbox_runtime/workspace",
)

#: The interpreter a console script in the bundle must name. ``uv pip install``
#: writes the shebang of whatever interpreter ran the build, which inside a
#: sandbox points at a path that does not exist -- and, worse, makes the bundle's
#: digest depend on where it was built, so a laptop and CI would disagree about
#: the version of byte-identical code. Both failures are invisible until a
#: sandbox tries to run ``lemma``.
SANDBOX_PYTHON = "/opt/lemma-python/bin/python"

#: The Python version that interpreter is (``Dockerfile.workspace``,
#: ``python:3.14.x``). The wheels are installed for it, not for whichever
#: Python runs this build -- the Windows host-pack build runs 3.12, which
#: the wheels' ``requires-python`` refuses.
SANDBOX_PYTHON_VERSION = "3.14"

#: A fixed timestamp for every zip entry. Any real mtime would make the archive
#: differ between two builds of identical sources, which is exactly what the
#: digest must not depend on.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

_PRUNE = ("__pycache__", ".pytest_cache", ".ruff_cache")


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}\n{result.stderr}"
        )


def _build_wheels(destination: Path, repo_root: Path) -> list[Path]:
    """One wheel per first-party project, built from the monorepo sources."""
    for project in WHEEL_PROJECTS:
        source = repo_root / project
        if not source.is_dir():
            raise SystemExit(f"first-party source is missing: {source}")
        _run(
            [
                "uv",
                "build",
                "--wheel",
                "--out-dir",
                str(destination),
                str(source),
            ]
        )
    wheels = sorted(destination.glob("*.whl"))
    if len(wheels) != len(WHEEL_PROJECTS):
        raise SystemExit(
            f"expected {len(WHEEL_PROJECTS)} wheels, built {len(wheels)}: "
            f"{[w.name for w in wheels]}"
        )
    return wheels


def _unpack(wheels: list[Path], site_packages: Path) -> None:
    """Unpack the wheels, resolving nothing.

    ``--no-deps`` because the sandbox image already carries the third-party
    closure and the install inside the sandbox must not need a network. If that
    stops being true, the installer's own smoke test inside the sandbox is what
    says so -- see ``REQUIRED_IMPORTS``.
    """
    site_packages.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--python-version",
            SANDBOX_PYTHON_VERSION,
            "--target",
            str(site_packages),
            *[str(wheel) for wheel in wheels],
        ]
    )


def _copy_runtime_sources(site_packages: Path, backend_root: Path) -> None:
    for relative in RUNTIME_SOURCES:
        source = backend_root / relative
        target = site_packages / relative
        if not source.exists():
            raise SystemExit(f"runtime source is missing: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*_PRUNE),
            )
        else:
            shutil.copy2(source, target)


def _script_body(source: Path) -> bytes:
    """A script's bytes with Unix line endings, whatever the checkout used.

    The Windows host pack builds this bundle too, and a checkout there may have
    rewritten every line to CRLF. A shebang ending in ``\\r`` names an
    interpreter that does not exist, and the digest would differ from a Unix
    build of the same sources.
    """
    return source.read_bytes().replace(b"\r\n", b"\n")


def _copy_scripts(payload: Path, backend_root: Path) -> None:
    """The image's scripts into ``bin/`` and ``lib/``, under their installed names.

    Every file in the scripts directory has to be classified -- shipped,
    library, or image-only -- so a new script cannot be left out of the overlay
    by nobody deciding.
    """
    source = backend_root / SCRIPTS_DIRECTORY
    present = {path.name for path in source.iterdir() if path.is_file()}
    classified = {*SCRIPT_NAMES, *SCRIPT_LIBRARIES, *IMAGE_ONLY_SCRIPTS}
    if unclassified := sorted(present - classified):
        raise SystemExit(
            f"scripts nobody decided about: {unclassified}; add each to "
            "SCRIPT_NAMES, SCRIPT_LIBRARIES or IMAGE_ONLY_SCRIPTS"
        )
    if missing := sorted(classified - present):
        raise SystemExit(f"scripts named but missing from {source}: {missing}")

    scripts = payload / "bin"
    scripts.mkdir(parents=True, exist_ok=True)
    for name, installed_as in sorted(SCRIPT_NAMES.items()):
        body = _script_body(source / name)
        for command in installed_as:
            target = scripts / command
            if target.exists():
                raise SystemExit(f"two things in the bundle are both bin/{command}")
            target.write_bytes(body)
            target.chmod(0o755)
    libraries = payload / "lib"
    libraries.mkdir(parents=True, exist_ok=True)
    for name in SCRIPT_LIBRARIES:
        target = libraries / name
        target.write_bytes(_script_body(source / name))
        target.chmod(0o644)


#: Installer bookkeeping that records *where this build ran*, not what it
#: produced. ``direct_url.json`` carries the ``file://`` URL of the wheel, which
#: is a temporary directory and therefore different on every build;
#: ``uv_cache.json`` is uv's own cache accounting. Neither changes what any
#: module does, and leaving them in made two builds of identical sources
#: disagree -- which would reinstall the whole fleet for nothing.
_PROVENANCE_FILES = ("direct_url.json", "uv_cache.json")


#: Where ``uv pip install --target`` puts console scripts inside the target:
#: ``bin`` on Unix, ``Scripts`` on Windows.
_INSTALLER_SCRIPT_DIRS = ("bin", "Scripts")


def _write_console_scripts(site_packages: Path, payload: Path) -> None:
    """Write the sandbox's console scripts from the wheels' entry points.

    Written here rather than taken from ``uv pip install --target``, which
    generates scripts for the machine running the build: a shebang naming the
    build's interpreter, and on Windows ``.exe`` launchers. Neither runs in a
    Linux sandbox, and either would make the bundle's identity depend on where
    it was built. The body is the one ``uv`` emits on Unix, so a bundle built
    anywhere is byte-identical.

    They go to ``<payload>/bin``, beside ``site-packages`` rather than inside
    it: ``current/site-packages`` for imports and ``current/bin`` for scripts,
    the layout the installed overlay presents.
    """
    for name in _INSTALLER_SCRIPT_DIRS:
        shutil.rmtree(site_packages / name, ignore_errors=True)
    scripts = payload / "bin"
    scripts.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry_points in sorted(site_packages.glob("*.dist-info/entry_points.txt")):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str  # type: ignore[assignment,method-assign]
        parser.read(entry_points, encoding="utf-8")
        if not parser.has_section("console_scripts"):
            continue
        for name, target in sorted(parser.items("console_scripts")):
            module, _, attribute = target.partition(":")
            if not module or not attribute:
                raise SystemExit(
                    f"{entry_points}: malformed entry point {name} = {target}"
                )
            imported = attribute.strip().split(".", 1)[0]
            script = scripts / name
            script.write_text(
                f"#!{SANDBOX_PYTHON}\n"
                "# -*- coding: utf-8 -*-\n"
                "import sys\n"
                f"from {module.strip()} import {imported}\n"
                'if __name__ == "__main__":\n'
                '    if sys.argv[0].endswith("-script.pyw"):\n'
                "        sys.argv[0] = sys.argv[0][:-11]\n"
                '    elif sys.argv[0].endswith(".exe"):\n'
                "        sys.argv[0] = sys.argv[0][:-4]\n"
                f"    sys.exit({attribute.strip()}())\n",
                encoding="utf-8",
                newline="\n",
            )
            script.chmod(0o755)
            written += 1
    if not written:
        raise SystemExit(f"no console scripts found in {site_packages}")


def _record_hash(path: Path) -> tuple[str, int]:
    """A ``RECORD`` entry's hash and size, in the format PEP 376 specifies."""
    payload = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return f"sha256={digest.decode('ascii').rstrip('=')}", len(payload)


def _normalise_dist_info(site_packages: Path) -> None:
    """Drop per-build bookkeeping, then reseal ``RECORD`` against what is left.

    Two things have to happen together. The provenance files vary per build, so
    they go. And the console scripts ``RECORD`` lists were replaced by
    ``_write_console_scripts``, so every surviving entry is recomputed
    from disk rather than trusted -- otherwise the bundle would ship a manifest
    that quietly disagreed with its own contents.
    """
    for dist_info in site_packages.glob("*.dist-info"):
        for name in _PROVENANCE_FILES:
            (dist_info / name).unlink(missing_ok=True)
        record = dist_info / "RECORD"
        if not record.is_file():
            continue
        resealed: list[str] = []
        for line in record.read_text(encoding="utf-8").splitlines():
            relative = line.split(",", 1)[0]
            if not relative:
                continue
            target = (dist_info.parent / relative).resolve()
            if target == record.resolve():
                # Its own entry carries no hash, by specification.
                resealed.append(f"{relative},,")
            elif target.is_file():
                digest, size = _record_hash(target)
                resealed.append(f"{relative},{digest},{size}")
            # Anything else was one of the provenance files, and is now gone.
        record.write_text("\n".join(resealed) + "\n", encoding="utf-8")


def _prune(root: Path) -> None:
    """Drop caches, which are build noise and would break reproducibility."""
    for name in _PRUNE:
        for path in root.rglob(name):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
    for compiled in root.rglob("*.pyc"):
        compiled.unlink(missing_ok=True)


def _archive_mode(path: Path) -> int:
    """The mode this file gets in the archive: executable, or not.

    Normalised rather than taken from disk, because the umask of whoever ran
    the build is not part of what the bundle is.
    """
    return 0o755 if path.stat().st_mode & 0o100 else 0o644


def _payload_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _contents_digest(root: Path) -> str:
    """A digest of what the bundle contains, independent of how it was packed.

    Taken over the unpacked tree rather than the archive because that is the
    thing two builds of identical sources agree about: zip metadata (order,
    timestamps, the compressor's choices) does not, and a version that moved
    without the code moving would reinstall the fleet for nothing.
    """
    digest = hashlib.sha256()
    for path in _payload_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        # The archive preserves the executable bit, so the identity has to as
        # well. Without it a bundle whose only change is `chmod +x` gets the
        # version it already had, and a sandbox holding that version skips the
        # install and keeps the old mode -- a script that is not executable and
        # a stamp insisting it is current.
        digest.update(_archive_mode(path).to_bytes(2, "big"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _verify_payload(site_packages: Path) -> None:
    """Every required package is present, is a package, and is not empty.

    A bundle missing one of these would degrade every sandbox it reached back to
    the baked copy, silently. This is a structural check rather than an import
    because the payload is built ``--no-deps`` -- see ``REQUIRED_IMPORTS``.
    """
    for relative in REQUIRED_PACKAGES:
        package = site_packages / relative
        if not package.is_dir():
            raise SystemExit(f"the staged bundle is missing {relative}")
        if not any(package.iterdir()):
            raise SystemExit(f"the staged bundle carries {relative} but it is empty")
    skills = site_packages / "lemma_cli" / "skills"
    if not any(skills.glob("*/SKILL.md")):
        raise SystemExit(
            f"the staged bundle carries {skills} with no skills in it; "
            "lemma-cli's vendoring did not run"
        )


def _write_archive(root: Path, destination: Path) -> str:
    """Zip the staged tree deterministically; return the archive's own sha256."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in _payload_files(root):
            info = zipfile.ZipInfo(
                path.relative_to(root).as_posix(), date_time=_ZIP_EPOCH
            )
            # Keep the executable bit and nothing else: a wheel's own modes vary
            # with the umask of whoever built it.
            info.external_attr = _archive_mode(path) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def _component_version(repo_root: Path) -> str:
    """The version the first-party projects agree on, for a human reading a log.

    Not the bundle's identity -- the digest is, and it is stronger, because two
    builds of one version can differ while two builds of one digest cannot. This
    is here so that "which release is this sandbox running" has an answer that
    does not require resolving a hash.
    """
    text = (repo_root / "lemma-python" / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "unknown"


def build(
    out_dir: Path,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    backend_root: Path = BACKEND_ROOT,
) -> dict[str, object]:
    """Build the bundle into ``out_dir``; return its manifest."""
    with tempfile.TemporaryDirectory() as raw:
        scratch = Path(raw)
        wheels = _build_wheels(scratch / "wheels", repo_root)
        site_packages = scratch / "payload" / "site-packages"
        _unpack(wheels, site_packages)
        _copy_runtime_sources(site_packages, backend_root)
        _write_console_scripts(site_packages, scratch / "payload")
        _copy_scripts(scratch / "payload", backend_root)
        _normalise_dist_info(site_packages)
        _prune(site_packages)
        _verify_payload(site_packages)

        payload = scratch / "payload"
        version = f"sha256:{_contents_digest(payload)}"
        archive_path = out_dir / "runtime-bundle.zip"
        archive_sha256 = _write_archive(payload, archive_path)
        manifest = {
            "version": version,
            "component_version": _component_version(repo_root),
            "archive_sha256": f"sha256:{archive_sha256}",
            "archive": archive_path.name,
            "requires": list(REQUIRED_IMPORTS),
            "contents": [
                path.relative_to(payload).as_posix() for path in _payload_files(payload)
            ],
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory to write runtime-bundle.zip and manifest.json into",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="Where lemma-python, lemma-cli and lemma-skills sit beside each other",
    )
    parser.add_argument(
        "--backend-root",
        type=Path,
        default=BACKEND_ROOT,
        help="Where sandbox_runtime lives",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = build(
        args.out_dir, repo_root=args.repo_root, backend_root=args.backend_root
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "contents"}, indent=2))


if __name__ == "__main__":
    main()
