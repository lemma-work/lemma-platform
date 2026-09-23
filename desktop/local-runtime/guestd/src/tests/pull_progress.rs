//! Reading a download's progress out of containerd's own records.

use crate::pull_progress::{
    manifest_blobs, parse_active, parse_size, platform_manifest, progress, PullProgress,
};
use serde_json::json;
use std::collections::{HashMap, HashSet};

#[test]
fn an_index_resolves_to_this_platforms_manifest() {
    let index = json!({"manifests": [
        {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
    ]});
    assert_eq!(
        platform_manifest(&index, "linux/arm64").as_deref(),
        Some("sha256:arm")
    );
    let manifest = json!({"layers": []});
    assert_eq!(platform_manifest(&manifest, "linux/arm64"), None);
}

#[test]
fn a_manifest_names_its_config_and_every_layer() {
    let manifest = json!({
        "config": {"digest": "sha256:c", "size": 10},
        "layers": [
            {"digest": "sha256:a", "size": 100},
            {"digest": "sha256:b", "size": 200},
        ],
    });
    assert_eq!(
        manifest_blobs(&manifest),
        Some(vec![
            ("sha256:c".to_owned(), 10),
            ("sha256:a".to_owned(), 100),
            ("sha256:b".to_owned(), 200),
        ])
    );
}

#[test]
fn active_ingests_are_read_whichever_way_the_size_is_spaced() {
    let text = "REF                     SIZE       AGE\n\
                layer-sha256:a          1.5MiB     2s\n\
                layer-sha256:b          512 KiB    1s\n\
                garbage\n";
    let active = parse_active(text);
    assert_eq!(active["sha256:a"], 1_572_864);
    assert_eq!(active["sha256:b"], 524_288);
    assert_eq!(active.len(), 2);
    assert_eq!(parse_size("7B"), Some(7));
    assert_eq!(parse_size("nonsense"), None);
}

#[test]
fn stored_blobs_count_whole_and_active_ones_up_to_their_size() {
    let blobs = vec![
        ("sha256:c".to_owned(), 10),
        ("sha256:a".to_owned(), 100),
        ("sha256:b".to_owned(), 200),
    ];
    let stored: HashSet<String> = ["sha256:c".to_owned()].into();
    let active: HashMap<String, u64> =
        [("sha256:a".to_owned(), 40), ("sha256:b".to_owned(), 999)].into();
    assert_eq!(
        progress(&blobs, &stored, &active),
        PullProgress {
            done: 250,
            total: 310
        }
    );
    assert_eq!(
        PullProgress {
            done: 1,
            total: 5 * 1024 * 1024
        }
        .sentence(),
        "1 MB of 5 MB"
    );
}
