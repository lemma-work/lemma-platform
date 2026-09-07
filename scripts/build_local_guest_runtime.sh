#!/usr/bin/env bash
# Build one architecture-specific, app-owned Linux guest artifact.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
target=""
output=""
guestd=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) target="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --guestd) guestd="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$target" || -z "$output" || -z "$guestd" ]]; then
  echo "usage: $0 --target <macos-aarch64|windows-x86_64> --guestd <path> --output <zip>" >&2
  exit 2
fi
mkdir -p "$(dirname "$output")"
output="$(cd "$(dirname "$output")" && pwd)/$(basename "$output")"
if [[ ! -x "$guestd" ]]; then
  echo "guest daemon is missing or not executable: $guestd" >&2
  exit 1
fi

case "$target" in
  macos-aarch64) docker_arch="arm64" ;;
  windows-x86_64) docker_arch="amd64" ;;
  *) echo "unsupported guest target: $target" >&2; exit 2 ;;
esac

work_dir="$(mktemp -d /tmp/lemma-guest-runtime.XXXXXX)"
# Docker-compatible VMs see macOS's physical /private/tmp path, not the
# user-facing /tmp symlink. Normalize once so later bind mounts address the
# same file from both the host and the build VM.
work_dir="$(cd "$work_dir" && pwd -P)"
assembly_container="lemma-guest-assembly-$(basename "$work_dir")"
cleanup() {
  docker rm -f "$assembly_container" >/dev/null 2>&1 || true
  rm -rf "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
context="$work_dir/context"
rootfs="$work_dir/rootfs"
rootfs_tar="$work_dir/rootfs.tar"
artifact="$work_dir/artifact/$target"
mkdir -p "$context" "$rootfs" "$artifact"
cp "$repo_root/desktop/local-runtime/guest-image/Dockerfile" "$context/Dockerfile"
cp -R "$repo_root/desktop/local-runtime/guest-image/rootfs-overlay" "$context/rootfs-overlay"
cp "$guestd" "$context/lemma-guestd"

docker buildx build \
  --platform "linux/$docker_arch" \
  --build-arg "GUEST_PLATFORM=$target" \
  --output "type=tar,dest=$rootfs_tar" \
  "$context"

if [[ "$target" == "macos-aarch64" ]]; then
  # Extract a host-side copy only to package the direct-boot initrd. The ext4
  # filesystem itself is assembled as root in Linux below so numeric ownership
  # from the OCI filesystem is preserved even when this runs on macOS.
  tar -xf "$rootfs_tar" -C "$rootfs"
  python3 "$repo_root/desktop/scripts/prepare_guest_boot.py" \
    --root "$rootfs" --output "$artifact"
  # Build within the product budget, then shrink the ext4 filesystem to its
  # actual contents plus 128 MiB of update headroom. The resulting RAW file is
  # sparse and is attached read-only by Lemma Desktop.
  truncate -s 2048M "$artifact/disk.raw"
  docker run --rm --name "$assembly_container" --platform "linux/$docker_arch" \
    --mount "type=bind,source=$rootfs_tar,target=/input/rootfs.tar,readonly" \
    --mount "type=bind,source=$artifact,target=/artifact" \
    ubuntu:24.04 \
    bash -euc '
      apt-get update >/dev/null
      apt-get install -y --no-install-recommends e2fsprogs >/dev/null
      mkdir -p /rootfs
      tar --numeric-owner -xf /input/rootfs.tar -C /rootfs
      rm -f /rootfs/etc/resolv.conf
      ln -s ../run/systemd/resolve/stub-resolv.conf /rootfs/etc/resolv.conf
      mkfs.ext4 -F -L lemma-root -d /rootfs /artifact/disk.raw
      e2fsck -fy /artifact/disk.raw >/dev/null
      resize2fs -M /artifact/disk.raw >/dev/null
      block_size="$(dumpe2fs -h /artifact/disk.raw 2>/dev/null | grep "^Block size:" | tr -dc "0-9")"
      block_count="$(dumpe2fs -h /artifact/disk.raw 2>/dev/null | grep "^Block count:" | tr -dc "0-9")"
      headroom_blocks="$((128 * 1024 * 1024 / block_size))"
      target_blocks="$((block_count + headroom_blocks))"
      # With no suffix resize2fs interprets the value in filesystem blocks.
      # Its "s" suffix means 512-byte sectors, which made this request eight
      # times too small on our 4 KiB ext4 image and failed the ARM64 build.
      resize2fs /artifact/disk.raw "$target_blocks" >/dev/null
      truncate -s "$((target_blocks * block_size))" /artifact/disk.raw
      e2fsck -fy /artifact/disk.raw >/dev/null
      test "$(stat -c %s /artifact/disk.raw)" -le "$((2048 * 1024 * 1024))"
    '
else
  mv "$rootfs_tar" "$artifact/rootfs.tar"
  rootfs_tar="$artifact/rootfs.tar"
fi

python3 - "$artifact/runtime.json" "$target" "$rootfs_tar" "$artifact" <<'PY'
import hashlib
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
rootfs_tar = pathlib.Path(sys.argv[3])
artifact = pathlib.Path(sys.argv[4])

def sha256(file):
    digest = hashlib.sha256()
    with file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

metadata = {
    "schema_version": 1,
    "target": sys.argv[2],
    "guest_protocol": 1,
    "engine": "containerd",
    "distribution": "ubuntu-24.04",
    "rootfs_sha256": sha256(rootfs_tar),
    "kernel_sha256": sha256(artifact / "vmlinuz") if (artifact / "vmlinuz").is_file() else None,
    "initrd_sha256": sha256(artifact / "initrd") if (artifact / "initrd").is_file() else None,
}
if sys.argv[2] == "macos-aarch64":
    metadata.update({
        "kernel_track": "ubuntu-" + (artifact / "kernel-release").read_text().strip(),
        "kernel_source": "Ubuntu archive: matching image, modules and initramfs",
        "packages_sha256": sha256(artifact / "packages.txt"),
    })
path.write_text(json.dumps(metadata, indent=2) + "\n")
PY

(cd "$work_dir/artifact" && zip -9 -r "$output" "$target")
python3 - "$output" <<'PY'
import hashlib
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
metadata = {
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "size": path.stat().st_size,
}
with __import__("zipfile").ZipFile(path) as archive:
    metadata["expanded_size"] = sum(entry.file_size for entry in archive.infolist())
    metadata["breakdown"] = {
        "kernel_bytes": sum(
            entry.file_size for entry in archive.infolist()
            if entry.filename.endswith("/vmlinuz")
        ),
        "initrd_bytes": sum(
            entry.file_size for entry in archive.infolist()
            if entry.filename.endswith("/initrd")
        ),
        "root_bytes": sum(
            entry.file_size for entry in archive.infolist()
            if entry.filename.endswith(("/disk.raw", "/rootfs.tar"))
        ),
        "metadata_bytes": sum(
            entry.file_size for entry in archive.infolist()
            if entry.filename.endswith(("/runtime.json", "/packages.txt", "/kernel-release"))
        ),
    }
path.with_suffix(path.suffix + ".json").write_text(json.dumps(metadata, indent=2) + "\n")
PY
