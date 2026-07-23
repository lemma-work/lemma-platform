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
trap 'rm -rf "$work_dir"' EXIT
context="$work_dir/context"
rootfs="$work_dir/rootfs"
rootfs_tar="$work_dir/rootfs.tar"
artifact="$work_dir/artifact/$target"
mkdir -p "$context" "$rootfs" "$artifact"
cp "$repo_root/local-runtime/guest-image/Dockerfile" "$context/Dockerfile"
cp -R "$repo_root/local-runtime/guest-image/rootfs-overlay" "$context/rootfs-overlay"
cp "$guestd" "$context/lemma-guestd"

docker buildx build \
  --platform "linux/$docker_arch" \
  --output "type=tar,dest=$rootfs_tar" \
  "$context"

if [[ "$target" == "macos-aarch64" ]]; then
  # Extract a host-side copy only to package the direct-boot kernel and initrd.
  # The ext4 filesystem itself is assembled as root in Linux below so numeric
  # ownership from the OCI filesystem is preserved even when this runs on macOS.
  tar -xf "$rootfs_tar" -C "$rootfs"
  kernel="$(find "$rootfs/boot" -maxdepth 1 -type f -name 'vmlinuz-*' | sort | tail -1)"
  initrd="$(find "$rootfs/boot" -maxdepth 1 -type f -name 'initrd.img-*' | sort | tail -1)"
  if [[ -z "$kernel" || -z "$initrd" ]]; then
    echo "guest image did not contain an Ubuntu kernel and initramfs" >&2
    exit 1
  fi
  # Apple Virtualization's direct boot path needs an uncompressed ARM64 Image.
  # Ubuntu publishes raw, gzip, and zstd zboot variants across kernel lines.
  python3 "$repo_root/scripts/extract_arm64_kernel.py" \
    --input "$kernel" \
    --output "$artifact/vmlinuz"
  cp "$initrd" "$artifact/initrd"
  truncate -s 2G "$artifact/disk.raw"
  docker run --rm --platform linux/amd64 \
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
    '
else
  mv "$rootfs_tar" "$artifact/rootfs.tar"
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

path.write_text(json.dumps({
    "schema_version": 1,
    "target": sys.argv[2],
    "guest_protocol": 1,
    "engine": "containerd",
    "distribution": "ubuntu-24.04",
    "rootfs_sha256": sha256(rootfs_tar),
    "kernel_sha256": sha256(artifact / "vmlinuz") if (artifact / "vmlinuz").is_file() else None,
    "initrd_sha256": sha256(artifact / "initrd") if (artifact / "initrd").is_file() else None,
}, indent=2) + "\n")
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
path.with_suffix(path.suffix + ".json").write_text(json.dumps(metadata, indent=2) + "\n")
PY
