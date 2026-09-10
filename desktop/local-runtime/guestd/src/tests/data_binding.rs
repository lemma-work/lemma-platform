//! The durable trees, and refusing to run without them.

use super::*;

/// Every wsl.exe call ended in an fstab error, in the one log Windows has.
///
/// WSL reads `/etc/fstab` on every start, and its first line is a virtiofs
/// share only the VZ guest has. It cannot mount there, so WSL appended
/// "Processing /etc/fstab with mount -a failed." to the output of every
/// single invocation -- including into `logs/wsl.log`, which is one of the
/// only diagnostics a Windows installation produces.
///
/// Measured on the machine before deciding what to do about it: `mount -a`
/// exits 32 and carries on, so `/tmp` was still the expected tmpfs and
/// nothing was actually broken. Which is why the fix is to stop WSL
/// reading a file written for another hypervisor, and to mount the one
/// entry that does apply from the WSL entry point -- not to change what
/// the VZ guest does, where the file is correct.
#[test]
fn wsl_does_not_read_an_fstab_written_for_the_vz_guest() {
    let conf = include_str!("../../../guest-image/rootfs-overlay/etc/wsl.conf");
    let init = include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-runtime-init");
    let fstab = include_str!("../../../guest-image/rootfs-overlay/etc/fstab");

    assert!(
        conf.lines()
            .any(|line| line.split_whitespace().collect::<String>() == "mountFsTab=false"),
        "WSL must not process an fstab whose first entry it can never mount: {conf}"
    );
    // The entry that does apply on WSL, moved to where WSL will run it.
    assert!(
        init.contains("mount -t tmpfs") && init.contains("/tmp"),
        "with fstab unread, the guest's own entry point owns /tmp"
    );
    for option in ["nosuid", "nodev", "noexec", "size=512m"] {
        assert!(
            init.contains(option),
            "/tmp keeps the properties fstab gave it, including {option}"
        );
    }
    // Unchanged, because on the VZ guest it is right.
    assert!(fstab.contains("virtiofs") && fstab.contains("tmpfs /tmp tmpfs"));
}

/// A missing data disk must stop the guest, not be waved through.
///
/// `ConditionPathExists=/dev/nvme0n1` reads like a safety check and is the
/// opposite of one. A failed `Condition*` makes systemd skip the unit and
/// record it as *started successfully*, so `Requires=lemma-data.service`
/// in containerd and lemma-guestd was satisfied by a data disk nobody had
/// mounted. Both started, and every workspace, database and volume went to
/// the root filesystem -- which this image is immutable over and discards
/// on the next boot. Each run reported healthy.
///
/// `Assert*` fails the unit instead, and that failure propagates through
/// those `Requires` and stops the services that would have written to the
/// wrong disk.
/// The holder does not stand in every door into the guest.
///
/// Anything that runs a command in a WSL distribution starts it, and the
/// bridge does exactly that for every request. Nothing on that path runs
/// `lemma-runtime-init`, so a distribution restarted that way has no binds
/// and `/var/lib/lemma` is an ordinary directory on the disk the next
/// upgrade deletes. A mutation served through that door would put work
/// somewhere it will not survive, and report success.
#[test]
fn a_guest_whose_data_is_not_bound_refuses_to_write() {
    // Says nothing off WSL -- macOS, and this test host.
    refuse_unbound_data().expect("not a WSL guest, nothing to be wrong about");

    // Scoped on `/mnt/wsl`, which every WSL distribution has, and not on
    // the share itself. Scoping on the share was a hole in the exact case
    // the guard exists for: one that was never published leaves no
    // directory, so the check read as "no holder, not applicable" and let
    // the write through to the disk the next upgrade deletes.
    let source = guest_source();
    let guard_body = &source[source
        .find("fn refuse_unbound_data")
        .expect("the guard exists")..];
    let guard_body = &guard_body[..guard_body.find("\n}\n").expect("it ends")];
    // Through `guest_is_wsl`, which is where that question is answered now --
    // the shutdown path asks it too, and two spellings of "this guest is WSL"
    // is one more than there should be.
    assert!(
        guard_body.contains("guest_is_wsl()"),
        "the platform check has to be the shared one:\n{guard_body}"
    );
    let answer = &source[source
        .find("pub(crate) fn guest_is_wsl")
        .expect("the shared check exists")..];
    let answer = &answer[..answer.find("\n}\n").expect("it ends")];
    assert!(
        answer.contains("Path::new(\"/mnt/wsl\").is_dir()"),
        "and it has to be /mnt/wsl, not the share:\n{answer}"
    );
    assert!(
        !answer.contains("lemma-data")
            && !guard_body.contains("Path::new(\"/mnt/wsl/lemma-data\")"),
        "an absent share is the failure, not a reason to skip",
    );

    // The guard is only worth having if it runs before the work, so this
    // pins where it sits rather than only that it exists.
    let source = guest_source();
    let dispatch = source
        .find("match request.operation.as_str()")
        .expect("the dispatch exists");
    let guard = source
        .find("refuse_unbound_data()?")
        .expect("mutations are guarded");
    assert!(
        guard < dispatch,
        "the guard has to run before the operation it is guarding"
    );

    let observation_only =
        source[guard.saturating_sub(200)..guard].contains("!is_observation(&request.operation)");
    assert!(
        observation_only,
        "reads have to keep answering: health is how the host learns the \
         guest is still coming up, and refusing it would turn a starting \
         runtime into a dead one"
    );
}

/// On Windows the data used to live inside the replaceable distribution.
///
/// Everything durable -- workspaces, databases, the container store -- sat
/// on the runtime distribution's own ext4.vhdx, which every upgrade
/// replaces wholesale. The upgrade could therefore only refuse, and did,
/// which pinned Windows users to whichever release they installed first.
/// The data now lives in a second distribution that publishes it into the
/// WSL VM's shared namespace, and this is the init that consumes it.
///
/// Refusing when the share is absent is the load-bearing half. Carrying on
/// would put the data back inside the distribution the next upgrade
/// deletes, and every run until that upgrade would look perfectly healthy.
#[test]
fn windows_will_not_start_without_the_data_holder() {
    let init = include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-runtime-init");

    // A mountpoint, not a directory: `/mnt/wsl` is a tmpfs, so a publish
    // that made the path and then failed to bind leaves a real directory
    // on storage the VM discards, and binding the data out of *that*
    // passes every check below while losing the work.
    let check = init
        .find("if ! /usr/bin/mountpoint -q \"$LEMMA_DATA_SHARE\"")
        .expect("it checks the share is really the published bind");
    let bind = init
        .find("/usr/local/bin/lemma-bind-data")
        .expect("it binds the data from the share");
    let containerd = init
        .find("mkdir -p /run/containerd")
        .expect("it starts containerd");

    assert!(
        check < bind && bind < containerd,
        "the share is checked, then bound, and only then does anything \
         start that writes to it: {init}"
    );
    assert!(
        init.contains("lemma-data: needs-repair:"),
        "the refusal has to reach the host's own detector: {init}"
    );
}

/// One implementation of the binds, for both platforms.
///
/// The last thing that script does is the gate that says the data is not
/// where it must be. A second copy of it that drifted would be a guest
/// that looks healthy while throwing work away, on whichever platform got
/// the stale one -- so the two reach a data root differently and then run
/// exactly the same code.
#[test]
fn both_platforms_bind_the_data_through_the_same_script() {
    let mount = include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-mount-data");
    let bind = include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-bind-data");
    let init = include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-runtime-init");

    for (name, script) in [("lemma-mount-data", mount), ("lemma-runtime-init", init)] {
        assert!(
            script.contains("/usr/local/bin/lemma-bind-data"),
            "{name} has to hand over rather than keep its own copy"
        );
        assert!(
            !script.contains("mount --bind"),
            "{name} still binds the data itself, which is the second copy: {script}"
        );
    }
    for required in [
        "/var/lib/lemma",
        "/var/lib/containerd",
        "/var/lib/nerdctl",
        "/etc/cni/net.d",
    ] {
        assert!(
            bind.contains(required),
            "the shared script has to bind {required}"
        );
    }
    assert!(
        bind.contains("lemma-data: needs-repair:"),
        "and it has to keep the gate that says the data did not land"
    );
}

/// A diagnosis written where the host cannot read it is not a diagnosis.
///
/// The host's `guest_needs_data_repair` greps the serial console for
/// `lemma-data: needs-repair:`, and that verdict is what turns a failed
/// start into a named cause and an offer to reset. Under systemd's default
/// `StandardOutput=journal` every one of those lines went to the guest's
/// journal instead, which nothing on the host reads: the VZ kernel command
/// line sets no `forward_to_console`, and the Windows guest runs with
/// systemd off entirely. So the detector could not fire, and the host
/// waited out its full readiness budget and said "did not become ready"
/// for failures the guest had already diagnosed precisely.
///
/// Confirmed on a real VZ guest before this was fixed: its console log
/// holds systemd's own "Finished lemma-data.service" line and not one byte
/// of the `mkfs.ext4` that same unit had just run.
#[test]
fn a_repair_verdict_has_to_reach_the_channel_the_host_reads() {
    const MARKER: &str = "lemma-data: needs-repair:";
    const CONSOLE: &str = "StandardOutput=journal+console";
    for (name, script, unit) in [
        (
            "lemma-mount-data",
            include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-mount-data"),
            include_str!("../../../guest-image/rootfs-overlay/etc/systemd/system/lemma-data.service"),
        ),
        (
            "lemma-data-unavailable",
            include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-data-unavailable"),
            include_str!(
                "../../../guest-image/rootfs-overlay/etc/systemd/system/lemma-data-unavailable.service"
            ),
        ),
    ] {
        assert!(
            script.contains(MARKER),
            "{name} is only listed here because it prints the marker"
        );
        assert!(
            unit.lines().any(|line| line.trim() == CONSOLE),
            "{name} prints a verdict the host reads off the console, so its                  unit has to write there: {unit}"
        );
    }
}

/// The one failure the mount script cannot report is its own absence.
///
/// `AssertPathExists` fails the unit before `ExecStart`, so no
/// `needs-repair:` line is ever printed and the host falls back to a flat
/// 120-second timeout. `OnFailure=` is the only hook that still runs.
#[test]
fn a_data_disk_that_never_appeared_is_named_rather_than_timed_out() {
    let unit =
        include_str!("../../../guest-image/rootfs-overlay/etc/systemd/system/lemma-data.service");
    assert!(
        unit.lines()
            .any(|line| line.trim() == "OnFailure=lemma-data-unavailable.service"),
        "nothing else runs when the assertion fails: {unit}"
    );

    let notice =
        include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-data-unavailable");
    assert!(
        notice.contains("[ ! -e /dev/nvme0n1 ]"),
        "OnFailure also fires for failures the mount script already                  diagnosed, and the host reads the last marker -- so this must not                  print over a precise reason: {notice}"
    );

    // An overlay file arrives without its executable bit; the Dockerfile
    // grants it. A notice that cannot run leaves exactly the silence it
    // was added to break.
    let dockerfile = include_str!("../../../guest-image/Dockerfile");
    assert!(
        dockerfile.contains("/usr/local/bin/lemma-data-unavailable"),
        "the notice has to be made executable in the image: {dockerfile}"
    );
}

#[test]
fn a_missing_data_disk_fails_the_guest_rather_than_being_skipped() {
    let unit =
        include_str!("../../../guest-image/rootfs-overlay/etc/systemd/system/lemma-data.service");
    let directives: Vec<&str> = unit
        .lines()
        .map(str::trim)
        .filter(|line| !line.starts_with('#'))
        .collect();

    assert!(
        directives.contains(&"AssertPathExists=/dev/nvme0n1"),
        "the data disk's absence has to fail the unit: {unit}"
    );
    assert!(
        !directives.iter().any(|line| line.starts_with("Condition")),
        "a Condition on this unit is recorded as success and silently              routes user data to the throwaway root filesystem: {unit}"
    );

    for (name, consumer) in [
        (
            "containerd",
            include_str!(
                "../../../guest-image/rootfs-overlay/etc/systemd/system/containerd.service"
            ),
        ),
        (
            "lemma-guestd",
            include_str!(
                "../../../guest-image/rootfs-overlay/etc/systemd/system/lemma-guestd.service"
            ),
        ),
    ] {
        assert!(
            consumer
                .lines()
                .any(|line| line.starts_with("Requires=")
                    && line.contains("lemma-data.service")),
            "{name} writes to the data disk, so it must require the unit                  that mounts it -- ordering alone lets it start without one"
        );
    }
}

/// Mounting can appear to succeed and still leave the data on the root.
///
/// Each bind is guarded by `mountpoint -q`, so a target that is already a
/// mountpoint is accepted and skipped. If that mountpoint belongs to the
/// root filesystem the paths all look right and the work is thrown away on
/// the next boot. The script checks the four of them before it exits.
///
/// Pinned as text, like the format guard above: a shell script inside a
/// disk image cannot be unit-tested from here, and the alternative is
/// booting a VM without a data disk, which only the qualification lane can
/// do.
#[test]
fn the_guest_refuses_to_finish_mounting_with_the_binds_missing() {
    let mount_data =
        include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-bind-data");
    let verification = mount_data
        .rfind("for required in")
        .expect("the mount script must verify its bind mounts before exiting");
    let last_bind = mount_data
        .rfind("mount --bind")
        .expect("the mount script binds the data disk into place");
    assert!(
        last_bind < verification,
        "the check has to run after the binds it is checking"
    );
    for required in [
        "/var/lib/lemma",
        "/var/lib/containerd",
        "/var/lib/nerdctl",
        "/etc/cni/net.d",
    ] {
        assert!(
            mount_data[verification..].contains(required),
            "{required} holds user data and must be confirmed to be on the                  data disk before the guest is allowed to come up"
        );
    }
    assert!(
        mount_data[verification..].contains("exit 1"),
        "reporting is not enough; a guest whose data is on the root              filesystem must not start"
    );
}

/// The guest must never force a filesystem onto a disk that has one.
///
/// `mkfs.ext4 -F` on `/dev/vdb` destroys every database, volume and
/// workspace on the machine, and the branch that reached it treated a
/// *corrupt* superblock exactly like an empty disk -- so the one situation
/// where the data most needed preserving was the situation that destroyed
/// it, silently, after which the app reported a healthy first run.
///
/// A shell script cannot be unit-tested here, so this pins its text. The
/// negative assertion is the one that matters: anything reintroducing `-F`
/// fails this, and the failure names why.
#[test]
fn the_guest_never_force_formats_a_disk_that_already_holds_a_filesystem() {
    let mount_data =
        include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-mount-data");

    assert!(
        !mount_data.contains("mkfs.ext4 -F"),
        "forcing a filesystem over an unrecognised disk destroys user data; \
         format only an unsigned disk the host says it just created",
    );
    assert!(
        mount_data.contains("/mnt/lemma-control/data-disk-fresh"),
        "only the host knows whether this disk was just created, so the \
         guest must consult its marker before formatting",
    );
    assert!(
        mount_data.contains("e2fsck -p"),
        "the stop path ends in SIGKILL, so dirty filesystems are guaranteed \
         and must be repaired rather than accumulated",
    );
    assert!(
        mount_data.contains("noatime,discard"),
        "without discard the sparse data disk only ever grows: space freed \
         inside the guest is never returned to macOS",
    );
    assert!(
        mount_data.contains("needs-repair:"),
        "an unmountable disk must announce itself on the console, or the \
         host waits out a 120-second timeout and reports nothing useful",
    );
}
