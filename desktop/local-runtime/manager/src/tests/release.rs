//! Telling one guest release from another.

use super::*;

#[cfg(target_os = "macos")]
#[test]
fn immutable_release_requires_all_boot_artifacts() {
    let root = tempdir().unwrap();
    let release = root.path().join("release");
    fs::create_dir_all(&release).unwrap();
    for name in ["vmlinuz", "initrd", "disk.raw"] {
        fs::write(release.join(name), format!("{name}-contents")).unwrap();
    }
    fs::write(
        release.join("runtime.json"),
        br#"{"service_transport_version":1}"#,
    )
    .unwrap();
    validate_macos_release(&release).unwrap();
    fs::remove_file(release.join("disk.raw")).unwrap();
    assert!(validate_macos_release(&release).is_err());
}

/// Two releases can ship archives of exactly the same size.
///
/// `tar` pads every member to a 512-byte block and the archive to the
/// blocking factor, so archive size is coarsely quantised: a guest-side
/// change of a few bytes in a shell script -- which is what most of them
/// are -- routinely lands on a byte-identical size. Reading that as "same
/// release" leaves this release's host talking to the previous release's
/// guestd, which is the failure this stamp exists to prevent.
///
/// The installer already wrote the signed manifest's digest beside the
/// release, so the exact answer is on disk for the cost of a small read.
#[test]
fn two_releases_of_the_same_size_are_still_two_releases() {
    let root = tempdir().unwrap();
    let stamp_of = |digest: &str| {
        let release = root.path().join(digest);
        let target = release.join("managed-runtime/windows-x86_64");
        fs::create_dir_all(&target).unwrap();
        let rootfs = target.join("rootfs.tar");
        // Byte-identical size, as tar's padding makes likely.
        fs::write(&rootfs, vec![0u8; 10240]).unwrap();
        fs::write(
            release.join(".lemma-runtime-artifacts.json"),
            format!("{{\"schema_version\":1,\"guest_sha256\":\"{digest}\"}}"),
        )
        .unwrap();
        rootfs_stamp(&rootfs).unwrap()
    };

    assert_ne!(
        stamp_of("aaaa1111"),
        stamp_of("bbbb2222"),
        "size alone cannot tell these apart, and the recorded digest can"
    );
}

/// Nothing recorded is not an error, it is the older layout.
#[test]
fn a_release_with_no_recorded_digest_still_has_a_stamp() {
    let root = tempdir().unwrap();
    let target = root.path().join("managed-runtime/windows-x86_64");
    fs::create_dir_all(&target).unwrap();
    let rootfs = target.join("rootfs.tar");
    fs::write(&rootfs, b"a guest filesystem").unwrap();

    assert_eq!(rootfs_stamp(&rootfs).unwrap(), "18");
}

/// Re-writing the same archive must not look like a different release.
///
/// A mismatch here prompts the user to reset the Windows runtime, which
/// deletes the distribution their workspaces and databases live in. The
/// stamp used to include modification time, so a repair, a re-download or a
/// reinstall of the identical release all changed it — and offered to
/// destroy the only copy of the data over a timestamp.
#[test]
fn a_rootfs_rewritten_in_place_is_still_the_same_guest() {
    let root = tempdir().unwrap();
    let rootfs = root.path().join("rootfs.tar");
    fs::write(&rootfs, b"a guest filesystem").unwrap();
    let before = rootfs_stamp(&rootfs).unwrap();

    // What a repair or a re-download does: identical bytes, written again.
    std::thread::sleep(std::time::Duration::from_millis(1100));
    fs::write(&rootfs, b"a guest filesystem").unwrap();

    assert_eq!(
        rootfs_stamp(&rootfs).unwrap(),
        before,
        "the same archive must not read as a different release"
    );
}

/// It must still notice a genuinely different one, or the check is theatre.
#[test]
fn a_different_rootfs_is_still_recognised_as_different() {
    let root = tempdir().unwrap();
    let rootfs = root.path().join("rootfs.tar");
    fs::write(&rootfs, b"the guest from 0.7.2").unwrap();
    let before = rootfs_stamp(&rootfs).unwrap();

    fs::write(&rootfs, b"the rather larger guest from 0.8.0").unwrap();

    assert_ne!(rootfs_stamp(&rootfs).unwrap(), before);
}

#[cfg(target_os = "macos")]
#[test]
fn incompatible_service_transport_is_rejected_without_changing_the_release() {
    let root = tempdir().unwrap();
    for name in ["vmlinuz", "initrd", "disk.raw"] {
        fs::write(root.path().join(name), b"keep").unwrap();
    }
    for metadata in [
        r#"{}"#,
        r#"{"service_transport_version":0}"#,
        r#"{"service_transport_version":2}"#,
    ] {
        fs::write(root.path().join("runtime.json"), metadata).unwrap();
        let error = validate_macos_release(root.path()).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::Unsupported);
        assert!(error.to_string().contains("matching runtime update"));
        assert_eq!(fs::read(root.path().join("disk.raw")).unwrap(), b"keep");
        assert_eq!(
            fs::read_to_string(root.path().join("runtime.json")).unwrap(),
            metadata
        );
    }
}
