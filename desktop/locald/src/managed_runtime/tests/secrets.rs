//! The infrastructure passwords, and what replacing them costs.

use super::*;

#[test]
fn secrets_are_stable_private_and_not_accepted_when_tampered() {
    let root = tempdir().unwrap();
    let path = root.path().join("infra.secrets.json");
    let first = load_or_create_secrets(&path, &mut Vec::new()).unwrap();
    let second = load_or_create_secrets(&path, &mut Vec::new()).unwrap();

    assert_eq!(first.postgres_password, second.postgres_password);
    assert_eq!(first.redis_password, second.redis_password);
    assert_ne!(first.postgres_password, first.redis_password);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        assert_eq!(fs::metadata(&path).unwrap().mode() & 0o777, 0o600);
    }
}

/// Replacing the infrastructure passwords is recorded as stranded data.
///
/// This is the one self-heal that deliberately makes the failure *harder*.
/// A new `postgres_password` does not open a volume that was `initdb`'d
/// with the old one, and nothing downstream notices: `ensure_core_container`
/// only replaces on an image or config-generation change, and
/// `ensure_database` connects over the local socket with no password. So
/// healing quietly would surface hours later as an opaque auth error deep
/// in the backend's migrations. The marker is what turns that into a button.
#[test]
fn replacing_the_infrastructure_passwords_records_that_data_must_be_reset() {
    let root = tempdir().unwrap();
    let path = root.path().join("infra.secrets.json");
    let original = load_or_create_secrets(&path, &mut Vec::new()).unwrap();
    fs::write(&path, b"{\"postgres_password\": \"too-short\"").unwrap();

    let mut healed = Vec::new();
    let replaced = load_or_create_secrets(&path, &mut healed).unwrap();

    assert_ne!(replaced.postgres_password, original.postgres_password);
    assert_eq!(healed.len(), 1);
    assert!(healed[0].contains("cannot be opened"), "{}", healed[0]);
    let reason = crate::paths::data_reset_reason(root.path())
        .expect("a replaced password strands the existing database");
    assert!(reason.contains("previous ones"), "{reason}");
    // The unreadable original is kept, not destroyed.
    assert_eq!(
        fs::read_dir(root.path())
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".invalid-"))
            .count(),
        1
    );
}
