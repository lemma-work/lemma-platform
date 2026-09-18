#!/usr/bin/env python3
"""The first-party Python a workspace sandbox runs, as one content-addressed archive.

This exists so that shipping a Lemma code change stops requiring a new sandbox
template. On E2B the sandbox *is* the disk, and adopting a new template means
destroying the one that holds the user's files -- so every release that touched
``lemma-cli`` or ``lemma-python`` was a release that wiped workspaces. The
expensive parts of the image (Chromium, Node, the apt layer, pandas) change a
handful of times a year; the first-party payload changed on roughly a quarter of
all commits, and it is under two megabytes of pure Python.

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

#: The part of ``sandbox_runtime`` a workspace sandbox actually runs. The
#: workspace runtime itself is deliberately absent: an E2B sandbox serves no HTTP
#: of its own, and the relay is the one exception, which is why it was built as a
#: separate process in the first place. ``tasks.py`` is here because
#: ``browser_relay.app`` and ``browser_relay.stream_proxy`` both import it.
RUNTIME_SOURCES = (
    "sandbox_runtime/__init__.py",
    "sandbox_runtime/tasks.py",
    "sandbox_runtime/browser_relay",
)

#: Everything the overlay must be able to import once installed. Recorded in the
#: manifest and checked by the installer *inside the sandbox*, which is the only
#: place the third-party closure exists -- the bundle is built ``--no-deps``, so
#: importing ``lemma_cli.cli`` here would only report which of ``typer``,
#: ``rich`` and ``textual`` happen to be in whatever environment ran the build.
#: A gate that answers a different question from the one it appears to ask is
#: worse than no gate, so this file checks structure and the installer checks
#: behaviour.
REQUIRED_IMPORTS = (
    "lemma_sdk",
    "lemma_cli.cli",
    "lemma_pod_bundle",
    "sandbox_runtime.browser_relay",
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
)

#: The interpreter a console script in the bundle must name. ``uv pip install``
#: writes the shebang of whatever interpreter ran the build, which inside a
#: sandbox points at a path that does not exist -- and, worse, makes the bundle's
#: digest depend on where it was built, so a laptop and CI would disagree about
#: the version of byte-identical code. Both failures are invisible until a
#: sandbox tries to run ``lemma``.
SANDBOX_PYTHON = "/opt/lemma-python/bin/python"

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


#: Installer bookkeeping that records *where this build ran*, not what it
#: produced. ``direct_url.json`` carries the ``file://`` URL of the wheel, which
#: is a temporary directory and therefore different on every build;
#: ``uv_cache.json`` is uv's own cache accounting. Neither changes what any
#: module does, and leaving them in made two builds of identical sources
#: disagree -- which would reinstall the whole fleet for nothing.
_PROVENANCE_FILES = ("direct_url.json", "uv_cache.json")


def _relocate_scripts(site_packages: Path, payload: Path) -> None:
    """Lift the console scripts out of site-packages, to the payload root.

    ``uv pip install --target`` writes them to ``<target>/bin``, which here puts
    a ``bin`` directory *inside* site-packages -- importable, which it is not,
    and a confusing place to look for ``lemma``. Moving it up gives the layout
    the installed overlay actually presents: ``current/site-packages`` for
    imports and ``current/bin`` for scripts.
    """
    nested = site_packages / "bin"
    if not nested.is_dir():
        raise SystemExit(f"the staged bundle has no console scripts at {nested}")
    nested.rename(payload / "bin")


def _retarget_console_scripts(payload: Path) -> None:
    """Point every console script at the sandbox's interpreter.

    Rewritten rather than regenerated because the body ``uv`` emits is already
    correct -- it imports the entry point and calls it. Only the first line is
    wrong, and it is wrong in a way that both breaks the script and leaks the
    build machine into the bundle's identity.
    """
    scripts = payload / "bin"
    for script in sorted(scripts.iterdir()):
        if not script.is_file():
            continue
        lines = script.read_text(encoding="utf-8").splitlines(keepends=True)
        if not lines or not lines[0].startswith("#!"):
            raise SystemExit(f"{script} has no shebang to retarget")
        lines[0] = f"#!{SANDBOX_PYTHON}\n"
        script.write_text("".join(lines), encoding="utf-8")
        script.chmod(0o755)


def _record_hash(path: Path) -> tuple[str, int]:
    """A ``RECORD`` entry's hash and size, in the format PEP 376 specifies."""
    payload = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return f"sha256={digest.decode('ascii').rstrip('=')}", len(payload)


def _normalise_dist_info(site_packages: Path) -> None:
    """Drop per-build bookkeeping, then reseal ``RECORD`` against what is left.

    Two things have to happen together. The provenance files vary per build, so
    they go. And ``_retarget_console_scripts`` has already rewritten a script
    that ``RECORD`` carries a hash for, so every surviving entry is recomputed
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
            info.external_attr = (0o755 if path.stat().st_mode & 0o100 else 0o644) << 16
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
        _relocate_scripts(site_packages, scratch / "payload")
        _retarget_console_scripts(scratch / "payload")
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
