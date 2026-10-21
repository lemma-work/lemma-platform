#!/usr/bin/env python3
"""Stage the `lemma` CLI the Desktop app carries for its Agent Host.

A run for a hosted workspace has no host pack behind it: the server that
names the host pack's CLI to a local run is somewhere else, and a path it named
would be one on its own machine. So the app carries a CLI of its own release,
in ``Contents/Resources/lemma-cli``, and the Agent Host lets that exact binary
run without a permission request (``desktop/agent-host/src/lemma_cli.rs``).

Laid out exactly as the host pack lays out its CLI, by the same steps, so one
launcher serves both: ``python/`` (a standalone CPython), ``cli/`` (the CLI
and its locked dependencies) and ``bin/lemma``. The interpreter keeps only what
a command-line client imports; the pack's backend needs the rest.

The online app is too small to carry it, so a release also publishes it on its
own (``--archive``): a gzipped tar of the same tree and a manifest beside it,
``{version, platform, url, sha256, size}``. The Agent Host of that release
downloads it the first time a hosted run needs a CLI the app does not carry
(``desktop/agent-host/src/lemma_cli/download.rs``), from a manifest URL compiled
into the host -- so the manifest's name and the archive's place beside it are a
contract with that file.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_local_host_pack import (  # noqa: E402
    compile_python_runtime,
    install_interpreter,
    install_lemma_cli_into,
    prune_python_runtime,
    tree_size,
)

#: Parts of a standalone CPython that no `lemma` command runs: the GUI toolkit
#: and its Tcl/Tk libraries, pip and the other tools in `bin/`, CPython's own
#: test modules, and the static library an extension build would link against.
#: The `lemma` launcher starts `bin/python3` and nothing else in `bin/`.
CLI_UNUSED = (
    "bin/idle*",
    "bin/pip*",
    "bin/pydoc*",
    "bin/python*-config",
    "lib/python*/site-packages/pip",
    "lib/python*/site-packages/pip-*",
    "lib/python*/tkinter",
    "lib/python*/turtle.py",
    "lib/python*/pydoc_data",
    "lib/python*/config-*",
    "lib/python*/lib-dynload/_tkinter*",
    "lib/python*/lib-dynload/_test*",
    "lib/python*/lib-dynload/_ctypes_test*",
    "lib/python*/lib-dynload/xxlimited*",
    "lib/tcl*",
    "lib/tk*",
    "lib/itcl*",
    "lib/thread*",
    "lib/libtcl*",
    "lib/libtk*",
)


def prune_for_cli(python_root: Path) -> None:
    """The host pack's pruning, and what only a client can also do without."""
    prune_python_runtime(python_root)
    for pattern in CLI_UNUSED:
        for path in python_root.glob(pattern):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)


#: The first four bytes of a Mach-O file: thin 32/64-bit in either byte order,
#: and universal.
_MACHO_MAGIC = {
    bytes.fromhex(magic)
    for magic in ("feedface", "feedfacf", "cefaedfe", "cffaedfe", "cafebabe")
}


def macho_files(root: Path) -> list[Path]:
    """Every Mach-O under ``root``: the interpreter, its library, extensions."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            if handle.read(4) in _MACHO_MAGIC:
                found.append(path)
    return found


def sign(root: Path, identity: str) -> int:
    """Sign every Mach-O in the bundle, before the app around it is.

    Code inside an app bundle has to carry its own signature: signing the app
    refuses unsigned code nested in it, and notarization rejects it. With a
    real identity they get the hardened runtime and a timestamp, as every
    binary Apple notarizes must; ad hoc (``-``, a local build nobody
    notarizes) they get neither, because the runtime's library validation
    treats ad hoc libraries as another team's and would refuse to load them.
    """
    files = macho_files(root)
    hardened = [] if identity == "-" else ["--options", "runtime", "--timestamp"]
    for path in files:
        subprocess.run(
            ["codesign", "--force", *hardened, "--sign", identity, str(path)],
            check=True,
            capture_output=True,
        )
    return len(files)


def archive_name(platform: str) -> str:
    return f"lemma-cli-{platform}.tar.gz"


def manifest_name(platform: str) -> str:
    """What the Agent Host asks its release for (`RELEASE_MANIFEST_URL`)."""
    return f"lemma-cli-{platform}.json"


def write_archive(root: Path, output: Path) -> tuple[str, int]:
    """A gzipped tar of everything under ``root``, at its top level.

    Deterministic, so the same tree is the same digest: sorted entries, no
    owner, a fixed timestamp, and no name or time in the gzip header. Symbolic
    links stay links (the interpreter's ``python3`` is one) and modes are kept,
    which is what makes ``bin/lemma`` runnable once unpacked. Returns the
    archive's SHA-256 and size.
    """

    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = 0
        return info

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(
                fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT
            ) as tar:
                for path in sorted(root.rglob("*")):
                    tar.add(
                        path,
                        arcname=path.relative_to(root).as_posix(),
                        recursive=False,
                        filter=normalize,
                    )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return digest, output.stat().st_size


def release_manifest(
    *, version: str, platform: str, release_url: str, sha256: str, size: int
) -> dict[str, object]:
    """The manifest the Agent Host checks before it installs the archive.

    The archive's URL is in the same release folder as the manifest: the host
    refuses one anywhere else, so a manifest cannot send it to another release
    or another host.
    """
    base = release_url.rstrip("/")
    return {
        "version": version.removeprefix("v"),
        "platform": platform,
        "url": f"{base}/{archive_name(platform)}",
        "sha256": sha256,
        "size": size,
    }


def publish(
    root: Path, directory: Path, *, version: str, platform: str, release_url: str
) -> Path:
    """Write the archive and its manifest into ``directory``; return the manifest."""
    if not (root / "bin" / "lemma").is_file():
        raise SystemExit(f"not a staged CLI (no bin/lemma): {root}")
    sha256, size = write_archive(root, directory / archive_name(platform))
    manifest = release_manifest(
        version=version,
        platform=platform,
        release_url=release_url,
        sha256=sha256,
        size=size,
    )
    path = directory / manifest_name(platform)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def build(
    output: Path, version: str, python_root: Path | None, identity: str | None
) -> int:
    """Stage the CLI at ``output``, prove it starts, and return its size."""
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    executable = install_interpreter(output / "python", version, python_root)
    prune_for_cli(output / "python")
    compile_python_runtime(output / "python", executable)
    with tempfile.TemporaryDirectory(prefix="lemma-cli-wheels-") as wheels:
        install_lemma_cli_into(output, executable, Path(wheels))
    if identity is not None:
        signed = sign(output, identity)
        print(f"  signed {signed} Mach-O files as {identity!r}", flush=True)
    # By its own path, as the Agent Host runs it, and with nothing of the
    # builder's environment it might lean on.
    version_line = subprocess.run(
        [str(output / "bin" / "lemma"), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir()},
    ).stdout.strip()
    print(f"  staged {version_line} at {output}", flush=True)
    return tree_size(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default="3.14.2")
    parser.add_argument(
        "--python-root",
        type=Path,
        help="copy an existing exact-version standalone Python distribution",
    )
    parser.add_argument(
        "--sign",
        metavar="IDENTITY",
        help="codesign every Mach-O with this identity (`-` for ad hoc)",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        metavar="DIRECTORY",
        help="also write the release archive and its manifest into this directory",
    )
    parser.add_argument("--release-version", help="with --archive: e.g. 0.9.0")
    parser.add_argument("--platform", default="aarch64-apple-darwin")
    parser.add_argument(
        "--release-url",
        help="with --archive: the release's download folder, "
        "e.g. https://github.com/OWNER/REPO/releases/download/v0.9.0",
    )
    args = parser.parse_args()
    if args.archive and not (args.release_version and args.release_url):
        parser.error("--archive needs --release-version and --release-url")
    output = args.output.resolve()
    size = build(output, args.python, args.python_root, args.sign)
    print(f"  {size / (1024 * 1024):.1f} MiB", flush=True)
    if args.archive:
        manifest = publish(
            output,
            args.archive.resolve(),
            version=args.release_version,
            platform=args.platform,
            release_url=args.release_url,
        )
        print(f"  wrote {manifest}:", manifest.read_text(encoding="utf-8"), flush=True)


if __name__ == "__main__":
    main()
