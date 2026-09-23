//! The gate that decides whether an update may replace this installation.
//!
//! Split from `runtime.rs` when it crossed the file-size ratchet, and the split
//! is along the seam the bug ran down: everything here is about the *installed*
//! side of the compatibility comparison, which is the side nothing ever
//! supplied.

use super::*;

/// The half of the comparison nothing ever supplied.
///
/// `compatibility_with` was tested with a literal `Some(18)` for the installed
/// side, which is why a repository-wide search for the key it actually reads --
/// `dataCompatibility` -- found one read and zero writes, and every assertion
/// above still passed. Derived from the installed runtime's own manifest here,
/// which is the same field of the same file the release feed derives its number
/// from, so the two sides cannot drift apart.
#[test]
fn the_installed_postgres_major_is_read_from_the_runtime_that_is_installed() {
    // Both shapes are real. The published release manifest writes a string; the
    // copy inside an installed host pack writes `{ref, digest}`.
    let directory = tempfile::tempdir().expect("temp dir");
    std::fs::write(
        directory.path().join("release.json"),
        serde_json::to_vec(&serde_json::json!({
            "version": "0.8.0",
            "infra": {"postgres": "docker.io/pgvector/pgvector:0.8.3-pg18@sha256:abc"}
        }))
        .unwrap(),
    )
    .unwrap();
    assert_eq!(runtime_postgres_major(directory.path()), Some(18));

    let object = tempfile::tempdir().expect("temp dir");
    std::fs::write(
        object.path().join("release.json"),
        serde_json::to_vec(&serde_json::json!({
            "version": "0.7.2",
            "infra": {"postgres": {
                "ref": "docker.io/pgvector/pgvector:0.8.3-pg18",
                "digest": "sha256:19c89f76"
            }}
        }))
        .unwrap(),
    )
    .unwrap();
    assert_eq!(runtime_postgres_major(object.path()), Some(18));

    // A manifest that names no major answers None rather than a wrong number:
    // "unknown" refuses the update, and a guess would permit it.
    let absent = tempfile::tempdir().expect("temp dir");
    std::fs::write(
        absent.path().join("release.json"),
        br#"{"infra":{"postgres":"docker.io/postgres:16"}}"#,
    )
    .unwrap();
    assert_eq!(runtime_postgres_major(absent.path()), None);
    assert_eq!(
        runtime_postgres_major(std::path::Path::new("/nonexistent")),
        None
    );

    assert_eq!(postgres_major_of("pgvector/pgvector:0.8.3-pg18"), Some(18));
    assert_eq!(postgres_major_of("pgvector/pgvector:1.0-pg7"), Some(7));
    assert_eq!(postgres_major_of("docker.io/postgres:18"), None);
    assert_eq!(postgres_major_of(""), None);
}

/// The whole gate, from a real installation's shape to the answer the UI shows.
///
/// The installation this reproduces is the one that reported the bug: a config
/// carrying only `{release, root}` -- which is all `activate_installed_runtime`
/// has ever written -- against a feed that does carry its major. It answered
/// "unknown", which `ensure_update_preserves_data` then refused, so the button was
/// disabled and the banner said no data-preserving upgrade had been
/// established. Both numbers were 18.
#[test]
fn an_installation_that_recorded_nothing_can_still_be_found_compatible() {
    let directory = tempfile::tempdir().expect("temp dir");
    std::fs::write(
        directory.path().join("release.json"),
        br#"{"infra":{"postgres":{"ref":"pgvector/pgvector:0.8.3-pg18"}}}"#,
    )
    .unwrap();
    let installed = runtime_postgres_major(directory.path());
    assert_eq!(installed, Some(18));

    let feed = LemmaUpdateMetadata {
        postgres_major: Some(18),
        runtime_download_bytes: Some(698_614_170),
    };
    assert_eq!(feed.compatibility_with(installed), "compatible");
    assert!(
        crate::runtime_setup::ensure_update_preserves_data(
            false,
            true,
            installed,
            feed.postgres_major,
        )
        .is_ok(),
        "an installation whose data matches the offered release must be allowed to update"
    );
}

/// The gate's wiring, from the config an affected installation really has.
///
/// The two tests above drive the helpers directly, which is the same shape of
/// gap that let the original bug through: they pass whether or not anything
/// calls them. This one builds the config `activate_installed_runtime` has
/// always written -- `{release, root}`, no `dataCompatibility` -- and asks the
/// function the update gate asks.
#[test]
fn the_update_gate_answers_for_an_installation_that_recorded_nothing() {
    let support = tempfile::tempdir().expect("temp dir");
    let release_root = support.path().join("runtime/releases/0.7.2-71e83e39");
    std::fs::create_dir_all(release_root.join("local-runtime")).unwrap();
    std::fs::write(
        release_root.join("local-runtime/release.json"),
        br#"{"version":"0.7.2","infra":{"postgres":{"ref":"pgvector/pgvector:0.8.3-pg18"}}}"#,
    )
    .unwrap();

    let config = serde_json::json!({
        "installedRuntime": {
            "release": "0.7.2",
            "root": release_root.to_string_lossy(),
        }
    });

    assert_eq!(
        postgres_major_from_config(&config),
        Some(18),
        "an installation whose config predates dataCompatibility must still be answerable"
    );

    // And a config that recorded one is believed without touching the disk.
    let recorded = serde_json::json!({
        "installedRuntime": {"dataCompatibility": {"postgres_major": 16}}
    });
    assert_eq!(postgres_major_from_config(&recorded), Some(16));
    assert_eq!(postgres_major_from_config(&serde_json::json!({})), None);
}
