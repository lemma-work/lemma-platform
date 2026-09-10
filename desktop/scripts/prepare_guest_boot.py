#!/usr/bin/env python3
"""Stage a matched Ubuntu ARM64 kernel and initramfs from the built filesystem."""

import argparse
import gzip
from pathlib import Path
import re
import shutil


def prepare(root: Path, output: Path) -> str:
    release = (root / "etc/lemma/kernel-release").read_text().strip()
    if not re.fullmatch(r"[0-9][a-zA-Z0-9.+_-]+", release):
        raise ValueError("Invalid guest kernel release")
    modules = root / "usr/lib/modules" / release
    if not modules.is_dir():
        # Older, non-usrmerged distributions keep the same tree under /lib.
        modules = root / "lib/modules" / release
    if not (modules / "modules.dep").is_file():
        raise ValueError("The guest is missing modules for its kernel")
    kernel = root / "boot" / f"vmlinuz-{release}"
    initrd = root / "boot" / f"initrd.img-{release}"
    if not initrd.is_file() or initrd.stat().st_size == 0:
        raise ValueError("The guest is missing its matching initramfs")
    data = kernel.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data[56:60] != b"ARM\x64":
        raise ValueError("The guest kernel is not an ARM64 Linux Image")
    output.mkdir(parents=True, exist_ok=True)
    (output / "vmlinuz").write_bytes(data)
    shutil.copyfile(initrd, output / "initrd")
    shutil.copyfile(root / "etc/lemma/packages.txt", output / "packages.txt")
    (output / "kernel-release").write_text(release + "\n")
    return release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.root, args.output))


if __name__ == "__main__":
    main()
