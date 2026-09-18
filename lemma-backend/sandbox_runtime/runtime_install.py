"""Install a runtime bundle inside a sandbox, or report which one is installed.

This file is *uploaded* into a sandbox and run there by the image's own
interpreter, so it imports nothing but the standard library and nothing from
this package -- it has to work as a single file dropped into ``/tmp``. Keep it
that way: the moment it needs a sibling module, delivering it stops being one
``write_file``.

The shape is chosen so that no lock is needed anywhere. A bundle is named by a
digest of its contents, so two installers racing on the same version are writing
byte-identical trees, and the loser of the ``rename`` can simply use the winner's
work. Only the ``current`` symlink is shared mutable state, and ``os.replace``
moves it atomically.

**What gets wired is one file.** The image's ``lemma`` console script is a pure
launcher -- ``from lemma_cli.cli import main`` -- so putting the overlay ahead of
the baked environment on ``sys.path`` makes that same script run the new code.
No symlink is repointed and no ``PATH`` is edited, which is also what keeps the
baked copy a working floor: remove the overlay and the sandbox falls back to the
image's own version instead of losing ``lemma`` entirely.

The ``.pth`` is named to sort *before* ``lemma-workspace-overlay.pth``. Both
insert at position 0, so the later one ends up in front, and the user's own
``pip install --prefix`` tree must stay in front of ours.

Usage, from the backend::

    <python> runtime_install.py probe   --root /opt/lemma-runtime
    <python> runtime_install.py install --root /opt/lemma-runtime \\
        --archive /tmp/lemma-runtime.zip --version sha256:<hex>
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

#: Sorts before ``lemma-workspace-overlay.pth``; see the module docstring.
PTH_NAME = "lemma-runtime-overlay.pth"

#: Written inside a version directory once its contents are complete. Its
#: presence under ``current`` is the only claim that an install finished.
STAMP_NAME = ".stamp"

CURRENT_LINK = "current"

#: How many installed versions to keep. Two, so a rollback to the immediately
#: previous bundle is a symlink flip rather than a re-upload.
KEEP_VERSIONS = 2


def _site_packages(override: Path | None = None) -> Path:
    """Where this interpreter reads ``.pth`` files from.

    Overridable so a test can exercise the wiring against a temporary tree
    instead of writing into the environment running the test.
    """
    return override or Path(sysconfig.get_paths()["purelib"])


def _version_directory(root: Path, version: str) -> Path:
    """``sha256:<hex>`` names the directory ``sha256-<hex>``."""
    algorithm, _, digest = version.partition(":")
    if not algorithm or not digest or not digest.isalnum():
        raise SystemExit(f"not a usable bundle version: {version!r}")
    return root / f"{algorithm}-{digest}"


def probe(root: Path, *, site_packages: Path | None = None) -> dict[str, object]:
    """Which bundle is installed, and whether there is a baked copy behind it.

    ``baked`` decides the backend's failure policy: a sandbox that still carries
    the image's own first-party copy can degrade to it, while one that does not
    has no ``lemma`` at all and must fail its ensure rather than come up broken.
    """
    stamp = root / CURRENT_LINK / STAMP_NAME
    installed: str | None = None
    try:
        installed = stamp.read_text(encoding="utf-8").strip() or None
    except OSError:
        installed = None
    return {
        "version": installed,
        "baked": (_site_packages(site_packages) / "lemma_cli").is_dir(),
        "overlay": str(root / CURRENT_LINK / "site-packages"),
    }


def _extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            target = destination / entry.filename
            # A zip is attacker-controlled input in the general case, and this
            # runs as root. Refuse anything that resolves outside the target
            # rather than trusting the archive's own names.
            if not str(target.resolve()).startswith(str(destination.resolve())):
                raise SystemExit(
                    f"bundle entry escapes its directory: {entry.filename}"
                )
            bundle.extract(entry, destination)
            if entry.external_attr >> 16 & 0o111:
                target.chmod(0o755)


def _claim(incoming: Path, target: Path) -> None:
    """Move the staged tree into place, tolerating another installer winning.

    ``EEXIST``/``ENOTEMPTY`` means someone else already installed this exact
    digest. Their tree and ours are byte-identical by construction, so the
    correct response is to drop ours and carry on.
    """
    try:
        os.rename(incoming, target)
    except OSError as exc:
        if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
            raise
        shutil.rmtree(incoming, ignore_errors=True)


def _smoke_test(overlay: Path, requires: list[str]) -> None:
    """Import what the bundle promises, with the overlay actually in front.

    This is the check the build cannot make: the third-party closure only exists
    here, in the image. A bundle that cannot import is one that would leave every
    sandbox it reached silently running the older baked copy.
    """
    if not requires:
        return
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(overlay)
    result = subprocess.run(
        [sys.executable, "-c", "import " + ", ".join(requires)],
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"the installed bundle cannot import {requires}:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def _point_current_at(root: Path, target: Path) -> None:
    """Flip ``current`` atomically.

    ``ln -sfn`` unlinks and recreates, so a reader between the two sees no
    ``current`` at all. ``os.replace`` onto a symlink is one operation.
    """
    staging = root / f".current-{os.getpid()}"
    staging.unlink(missing_ok=True)
    staging.symlink_to(target.name)
    os.replace(staging, root / CURRENT_LINK)


def _write_pth(overlay: Path, site_packages: Path | None = None) -> None:
    """Put the overlay ahead of the baked environment for this interpreter."""
    site_packages = _site_packages(site_packages)
    line = (
        f'import sys; p="{overlay}"; '
        "sys.path.insert(0, p) if p not in sys.path else None\n"
    )
    path = site_packages / PTH_NAME
    if path.is_file() and path.read_text(encoding="utf-8") == line:
        return
    path.write_text(line, encoding="utf-8")


def _prune(root: Path, keep: Path) -> None:
    """Drop old versions and abandoned staging directories."""
    current = (root / CURRENT_LINK).resolve()
    versions = sorted(
        (path for path in root.glob("sha256-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in versions[KEEP_VERSIONS:]:
        if stale.resolve() not in (current, keep.resolve()):
            shutil.rmtree(stale, ignore_errors=True)
    for abandoned in root.glob(".incoming-*"):
        shutil.rmtree(abandoned, ignore_errors=True)


def install(
    *,
    root: Path,
    archive: Path,
    version: str,
    requires: list[str],
    site_packages: Path | None = None,
) -> dict[str, object]:
    already = probe(root, site_packages=site_packages)
    if already["version"] == version:
        return {"version": version, "installed": False, "reason": "already current"}

    root.mkdir(parents=True, exist_ok=True)
    target = _version_directory(root, version)
    if not (target / STAMP_NAME).is_file():
        incoming = root / f".incoming-{os.getpid()}"
        shutil.rmtree(incoming, ignore_errors=True)
        _extract(archive, incoming)
        # Written before the move, so a directory that exists is a directory
        # that is complete -- there is no window where a concurrent installer
        # could adopt a half-extracted tree.
        (incoming / STAMP_NAME).write_text(version, encoding="utf-8")
        _claim(incoming, target)

    # Only after this passes does anything point at the new tree.
    _smoke_test(target / "site-packages", requires)
    _point_current_at(root, target)
    _write_pth(root / CURRENT_LINK / "site-packages", site_packages)
    _prune(root, keep=target)
    # The archive is consumed, and only now is it safe to say so: before the
    # smoke test a failure still wants it on disk to retry against. Left behind
    # it is a superseded copy of every bundle this sandbox has ever been sent,
    # carried into every later snapshot -- and on a fabric where the sandbox is
    # the disk, that is the user's disk it is carried on.
    archive.unlink(missing_ok=True)
    return {"version": version, "installed": True}


def _emit(payload: dict[str, object]) -> None:
    """One JSON line on stdout: this process talks to a caller, not a reader."""
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or probe a runtime bundle")
    parser.add_argument("action", choices=("probe", "install"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--version")
    parser.add_argument(
        "--site-packages",
        type=Path,
        help="Override where the .pth is written; defaults to this interpreter's",
    )
    parser.add_argument(
        "--requires",
        default="",
        help="Comma-separated modules the installed bundle must import",
    )
    args = parser.parse_args(argv)

    if args.action == "probe":
        _emit(probe(args.root, site_packages=args.site_packages))
        return 0

    if args.archive is None or args.version is None:
        parser.error("install needs --archive and --version")
    outcome = install(
        root=args.root,
        archive=args.archive,
        version=args.version,
        requires=[item for item in args.requires.split(",") if item],
        site_packages=args.site_packages,
    )
    # And the installer itself, which is staged the same way and just as
    # superseded. Guarded on the staging directory so that running this file
    # from a checkout -- which is how its own tests drive it -- cannot delete
    # the source. Unlinking a running script is safe: the kernel holds the
    # inode until this process exits.
    script = Path(__file__).resolve()
    if script.is_relative_to(Path(tempfile.gettempdir())):
        script.unlink(missing_ok=True)
    _emit(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
