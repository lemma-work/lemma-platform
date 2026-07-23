#!/usr/bin/env bash
# Build one architecture-specific, app-owned Linux guest artifact.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
target=""
output=""
guestd=""
kata_version="3.17.0"
kata_kernel_version="6.12.28-153"
kata_archive_sha256="647c7612e6edf789d5e14698c48c99d8bac15ad139ffaa1c8bb7d229f748d181"
kata_archive_url="https://github.com/kata-containers/kata-containers/releases/download/${kata_version}/kata-static-${kata_version}-arm64.tar.xz"

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
  # Extract a host-side copy only to package the direct-boot initrd. The ext4
  # filesystem itself is assembled as root in Linux below so numeric ownership
  # from the OCI filesystem is preserved even when this runs on macOS.
  tar -xf "$rootfs_tar" -C "$rootfs"
  initrd="$(find "$rootfs/boot" -maxdepth 1 -type f -name 'initrd.img-*' | sort | tail -1)"
  if [[ -z "$initrd" ]]; then
    echo "guest image did not contain an Ubuntu initramfs" >&2
    exit 1
  fi

  # Apple Containerization pins Kata's container-optimized kernel for its own
  # Virtualization.framework runtime. Keep the archive and exact kernel version
  # immutable: distro kernel metapackages have regressed both virtio-vsock and
  # cgroup/runc workloads in clean Lemma appliance boots.
  kata_archive="$work_dir/kata-static-${kata_version}-arm64.tar.xz"
  kata_root="$work_dir/kata"
  mkdir -p "$kata_root"
  if [[ -n "${LEMMA_KATA_KERNEL_ARCHIVE:-}" ]]; then
    cp "$LEMMA_KATA_KERNEL_ARCHIVE" "$kata_archive"
  else
    curl -fsSLo "$kata_archive" "$kata_archive_url"
  fi
  python3 - "$kata_archive" "$kata_archive_sha256" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f"Kata kernel archive checksum mismatch: {actual}")
PY
  tar -xJf "$kata_archive" -C "$kata_root" \
    "./opt/kata/share/kata-containers/vmlinux-${kata_kernel_version}"
  cp "$kata_root/opt/kata/share/kata-containers/vmlinux-${kata_kernel_version}" \
    "$artifact/vmlinuz"
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

python3 - "$artifact/runtime.json" "$target" "$rootfs_tar" "$artifact" "$kata_kernel_version" "$kata_archive_url" "$kata_archive_sha256" <<'PY'
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
        "kernel_track": f"kata-{sys.argv[5]}",
        "kernel_source": sys.argv[6],
        "kernel_source_archive_sha256": sys.argv[7],
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
path.with_suffix(path.suffix + ".json").write_text(json.dumps(metadata, indent=2) + "\n")
PY
