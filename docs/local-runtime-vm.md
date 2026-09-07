# Running the Lemma Desktop guest VM by hand

The `lemma_local` provider talks to a Linux guest running under
Virtualization.framework. Unit and integration tests stub that guest, which is
fine for the provider's own logic but cannot catch a disagreement about the
wire format — and one such disagreement did ship: the stub returned a top-level
`sandbox_id` from `sandbox.list`, while the guest nests it at `status.id`, so
the orphan sweeper saw objects it could not name.

This is how to boot a real one on an Apple Silicon Mac and drive it.

## 1. Get the guest artifacts

No GitHub *release* has ever carried them — every release's `lemma-local.json`
has `host_packs` and `guest_runtimes` set to null. They are built by the
**Release Local Images** workflow (`publish: false`) and uploaded as a
14-day Actions artifact:

```bash
gh run download <RUN_ID> -n "lemma-local-test-<full-sha>" -D /tmp/lemma-desktop-runtime
```

Verify and unpack the macOS guest:

```bash
unzip -q /tmp/lemma-desktop-runtime/guest-runtimes/lemma-guest-runtime-macos-aarch64.zip -d /tmp/lemma-desktop-runtime/extracted
```

That yields `macos-aarch64/{vmlinuz,initrd,disk.raw,runtime.json,kernel-release,packages.txt}`.

The macOS appliance uses Ubuntu's ARM64 generic kernel and its matching module
packages. The build generates the initramfs from that same filesystem and stages
the exact ABI recorded in `kernel-release`; it does not select a second kernel
from an unrelated release. `packages.txt` records installed package versions,
and `runtime.json` records the boot-file and package-inventory hashes. WSL uses
the Windows-provided kernel and skips these boot packages.

The initramfs includes the virtual clock, shared-directory and vsock drivers.
The PL031 clock driver lives in Ubuntu's extra-modules package, so an otherwise
bootable minimal cloud image is not evidence that the clock is initialized.
Keep the host clock initialization until startup and sleep/wake clock behavior
have both been qualified.

The VM boots Ubuntu's `multi-user.target`, with Lemma's services enabled as
normal systemd units. This also starts `systemd-logind` so the helper's graceful
stop request can power off the guest. `lemma-runtime.target` only forwards older
helpers to that standard target; it no longer replaces the distribution's boot
sequence. The persistent-data unit waits for the control share before checking
the host's fresh-disk marker.

## 2. Build and sign the VZ helper

The virtualization entitlement is what makes this Mac-only and signature-bound:

```bash
swift build -c release --package-path desktop/local-runtime/macos-vz
codesign --force --sign "<your Apple Development identity>" --options runtime \
  --entitlements desktop/local-runtime/macos-vz/lemma-vz.entitlements.plist \
  desktop/local-runtime/macos-vz/.build/arm64-apple-macosx/release/lemma-vz
```

## 3. Prepare the runtime state and the trusted control share

`locald` normally does this. By hand it is two files and a sparse disk — sizes
and modes taken from `desktop/local-runtime/manager/src/lib.rs`:

```bash
mkdir -p /tmp/lemma-vz-state /tmp/lemma-vz-share
truncate -s 24G /tmp/lemma-vz-state/data.raw && chmod 600 /tmp/lemma-vz-state/data.raw
touch /tmp/lemma-vz-share/data-disk-fresh
openssl rand -hex 32 > /tmp/lemma-vz-share/guest.capability
date +%s > /tmp/lemma-vz-share/host.epoch
chmod 600 /tmp/lemma-vz-share/guest.capability /tmp/lemma-vz-share/host.epoch
```

Both share files are load-bearing. Without `host.epoch` the guest's
`lemma-host-clock.service` fails, and `lemma-guestd` is `Requires=` it — so the
VM boots to a login prompt with containerd up and no Lemma runtime at all.
Create `data-disk-fresh` only when creating a new, empty disposable disk, and
remove it after the first successful boot. Never mark an existing data disk as
fresh to work around a mount or repair failure.

## 4. Boot

```bash
desktop/local-runtime/macos-vz/.build/arm64-apple-macosx/release/lemma-vz serve \
  --release /tmp/lemma-desktop-runtime/extracted/macos-aarch64 \
  --runtime /tmp/lemma-vz-state \
  --control-socket /tmp/lemma-vz-state/control.sock \
  --control-share /tmp/lemma-vz-share
```

`/tmp/lemma-vz-state/console.log` should reach
`multi-user.target - Multi-User System`, and the `health` operation must succeed.
Only one VM may hold
`data.raw`; a second `serve` fails with "The storage device attachment is
invalid", which means a previous one is still running, not that the disk is bad.

## 5. Drive it

```bash
export LEMMA_GUEST_CONTROL_SOCKET=/tmp/lemma-vz-state/control.sock
export LEMMA_GUEST_CAPABILITY_FILE=/tmp/lemma-vz-share/guest.capability
echo '{"version":1,"operation":"sandbox.list","parameters":{}}' \
  | desktop/target/release/lemma-runtime request
```

Point the provider at the same two variables plus
`LEMMA_MANAGED_RUNTIME_CLI=<path to lemma-runtime>`. Images must be pinned by
digest; the ones matching a given build are in that run's `lemma-local.json`
under `images`.

## Teardown

Delete any sandboxes you created (`sandbox.delete`), stop `lemma-vz`, and
remove `/tmp/lemma-vz-state` — `data.raw` is 24 GiB sparse and will have grown.

## Isolate a kernel failure before testing the whole app

Use a stock Ubuntu ARM64 raw cloud filesystem, with the kernel and initramfs
extracted from that exact filesystem. Verify the published checksum signature
against Canonical's documented signing-key fingerprint before using the image.
This provides an independent reference without Lemma's daemon, control share,
memory controller, or persistent data disk.

Build the minimal runner:

```bash
swiftc desktop/scripts/reference-vm.swift -o /tmp/lemma-reference-vm
codesign --force --sign - \
  --entitlements desktop/local-runtime/macos-vz/lemma-vz.entitlements.plist \
  /tmp/lemma-reference-vm
```

Supply a NoCloud seed image whose `runcmd` performs the workload under test,
checks `/proc/sys/kernel/tainted` for machine-check, bad-page and Oops bits
(`taint & 176` must equal zero), and prints `LEMMA_REFERENCE_BOOT_OK` to
`/dev/console`. Configure cloud-init's `power_state` to power off after the
workload. The seed must not contain personal credentials. Then run:

```bash
uv run --no-project python desktop/scripts/check_vm_boot.py \
  --runner /tmp/lemma-reference-vm \
  --kernel /tmp/reference/Image \
  --initrd /tmp/reference/initrd \
  --disk /tmp/reference/root.raw \
  --seed /tmp/reference/seed.iso \
  --evidence /tmp/reference/boot-01
```

The checker clones the source disk, hashes its inputs, retains console output,
requires both the workload marker and a clean guest power-off, and stops and
reaps the runner on timeout or cancellation. Existing evidence directories are
never overwritten. A kernel panic fails the run even if a success marker was
printed earlier. The default root label is `cloudimg-rootfs`; use
`--command-line` for other images. Run a fresh evidence directory for every
comparison and change one input at a time. A different failure signature is
not a reproduction of the original defect.

A passing reference boot does not qualify the Lemma appliance. Repeat startup,
engine and clock checks through the production VZ helper, then test the exact
packaged app, existing data, container workloads, shutdown, and sleep/wake.

For the production helper, run the lifecycle check against an extracted guest
artifact and a signed helper from the same candidate:

```bash
uv run --no-project python desktop/scripts/check_guest_lifecycle.py \
  --helper /tmp/candidate/lemma-vz \
  --cli desktop/target/release/lemma-runtime \
  --release /tmp/candidate/macos-aarch64 \
  --evidence /tmp/candidate/lifecycle-01
```

This creates a private sparse data disk, checks engine readiness and clock
accuracy repeatedly, and restarts with the same disk after clearing the
fresh-disk marker. Every cycle must power off cleanly; a forced termination
fails qualification even when all health requests succeeded. Console faults
also fail the check, including faults observed during shutdown. The tool keeps
input hashes, per-boot console logs and result files, and owns process cleanup
on failure or cancellation. Run this on a Mac that supports
Virtualization.framework; unit-test CI alone cannot certify a bootable artifact.

Tooling regression tests run in `make desktop-test` and desktop CI:

```bash
uv run --no-project python -m unittest discover -s desktop/scripts -p 'test_*.py'
```

References: [Apple's Linux VM sample](https://developer.apple.com/documentation/virtualization/running-linux-in-a-virtual-machine),
[Canonical image verification](https://ubuntu.com/docs/public-images/public-images-how-to/verify-image-checksum/).
