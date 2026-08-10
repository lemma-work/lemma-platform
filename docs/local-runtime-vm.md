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

That yields `macos-aarch64/{vmlinuz,initrd,disk.raw,runtime.json}`.

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
openssl rand -hex 32 > /tmp/lemma-vz-share/guest.capability
date +%s > /tmp/lemma-vz-share/host.epoch
chmod 600 /tmp/lemma-vz-share/guest.capability /tmp/lemma-vz-share/host.epoch
```

Both share files are load-bearing. Without `host.epoch` the guest's
`lemma-host-clock.service` fails, and `lemma-guestd` is `Requires=` it — so the
VM boots to a login prompt with containerd up and no Lemma runtime at all.

## 4. Boot

```bash
desktop/local-runtime/macos-vz/.build/arm64-apple-macosx/release/lemma-vz serve \
  --release /tmp/lemma-desktop-runtime/extracted/macos-aarch64 \
  --runtime /tmp/lemma-vz-state \
  --control-socket /tmp/lemma-vz-state/control.sock \
  --control-share /tmp/lemma-vz-share
```

`/tmp/lemma-vz-state/console.log` should reach
`lemma-runtime.target - Lemma private local runtime`. Only one VM may hold
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
