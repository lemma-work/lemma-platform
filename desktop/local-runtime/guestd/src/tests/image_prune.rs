//! Pruning images nothing can run again, and trimming the data disk.

use super::*;

const WORKSPACE: &str = "ghcr.io/lemma/workspace@sha256:new";
const OLD_WORKSPACE: &str = "ghcr.io/lemma/workspace@sha256:old";

fn stored(name: &str) -> StoredImage {
    StoredImage {
        name: name.into(),
        digest: name.split_once('@').map(|(_, digest)| digest.into()),
    }
}

#[test]
fn short_references_compare_equal_to_the_names_the_engine_stores() {
    assert_eq!(
        normalize_reference("postgres@sha256:a"),
        "docker.io/library/postgres@sha256:a"
    );
    assert_eq!(
        normalize_reference("redis"),
        "docker.io/library/redis:latest"
    );
    assert_eq!(
        normalize_reference("supertokens/postgresql:9"),
        "docker.io/supertokens/postgresql:9"
    );
    // A registry port is not a tag.
    assert_eq!(
        normalize_reference("localhost:5000/lemma/x"),
        "localhost:5000/lemma/x:latest"
    );
    assert_eq!(normalize_reference(WORKSPACE), WORKSPACE);
}

#[test]
fn only_images_no_container_uses_and_no_release_names_are_pruned() {
    let images = [
        stored(WORKSPACE),
        stored(OLD_WORKSPACE),
        stored("ghcr.io/lemma/function@sha256:fn"),
        stored("docker.io/library/postgres@sha256:pg"),
        stored("docker.io/library/redis@sha256:stopped"),
        StoredImage {
            name: "<none>@<none>".into(),
            digest: None,
        },
        StoredImage {
            name: "".into(),
            digest: None,
        },
    ];
    let keep = vec![WORKSPACE.to_owned(), "postgres@sha256:pg".to_owned()];
    // A stopped sandbox is still "in use": it starts again from its image.
    let in_use = vec![
        "ghcr.io/lemma/function@sha256:fn".to_owned(),
        "redis@sha256:stopped".to_owned(),
    ];
    assert_eq!(
        images_to_prune(&images, &keep, &in_use),
        vec![OLD_WORKSPACE.to_owned()]
    );
}

#[test]
fn a_release_pinning_a_digest_keeps_that_image_under_any_name() {
    let images = [StoredImage {
        name: "mirror.example/lemma/workspace:0.8.0".into(),
        digest: Some("sha256:new".into()),
    }];
    assert!(images_to_prune(&images, &[WORKSPACE.to_owned()], &[]).is_empty());
}

#[test]
fn the_listing_is_read_by_name_or_rebuilt_from_its_parts() {
    let listing = [
        r#"{"Name":"ghcr.io/lemma/workspace@sha256:old","Repository":"ghcr.io/lemma/workspace","Tag":"<none>","Digest":"sha256:old"}"#,
        r#"{"Repository":"docker.io/library/redis","Tag":"<none>","Digest":"sha256:r"}"#,
        r#"{"Repository":"docker.io/library/busybox","Tag":"1","Digest":"sha256:b"}"#,
        r#"{"Repository":"<none>","Tag":"<none>","Digest":"sha256:d"}"#,
        "",
    ]
    .join("\n");
    let images = parse_stored_images(&listing).unwrap();
    let names: Vec<_> = images.iter().map(|image| image.name.as_str()).collect();
    assert_eq!(
        names,
        [
            "ghcr.io/lemma/workspace@sha256:old",
            "docker.io/library/redis@sha256:r",
            "docker.io/library/busybox:1",
        ]
    );
    assert!(parse_stored_images("not json").is_err());
}

#[test]
fn a_prune_lists_everything_first_and_removes_without_force() {
    let root = tempdir().unwrap();
    let listing = [OLD_WORKSPACE, WORKSPACE]
        .iter()
        .map(|name| json!({"Name": name, "Digest": name.split_once('@').unwrap().1}).to_string())
        .collect::<Vec<_>>()
        .join("\n");
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "docker.io/library/postgres@sha256:pg\n"),
            output(true, &listing),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let answer = service
        .prune_images(json!({"images": {
            "postgres": "postgres@sha256:pg",
            "redis": "redis@sha256:r",
            "supertokens": "supertokens@sha256:s",
            "workspace": WORKSPACE,
        }}))
        .unwrap();
    assert_eq!(answer["removed"], json!([OLD_WORKSPACE]));
    let commands = service.engine.commands();
    assert_eq!(commands[0][0], "ps");
    assert!(commands[0].contains(&"--all".to_owned()));
    assert_eq!(commands[1][0], "images");
    assert_eq!(commands[2], vec!["rmi", OLD_WORKSPACE]);
    assert_eq!(commands.len(), 3);
}

#[test]
fn a_prune_that_cannot_list_containers_removes_nothing() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(false, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let images = json!({"images": {"postgres": "p", "redis": "r", "supertokens": "s"}});
    assert!(service.prune_images(images).is_err());
    assert_eq!(service.engine.commands().len(), 1);
    assert!(service
        .prune_images(json!({"images": {}, "extra": 1}))
        .is_err());
}

#[test]
fn prune_and_trim_are_serialised_mutations() {
    assert!(!is_observation("core.prune_images"));
    assert!(!is_observation("core.trim"));
}

#[test]
fn fstrim_output_becomes_an_answer() {
    assert_eq!(
        trim_outcome(
            true,
            "/var/lib/lemma-data: 1.2 GiB (1288490188 bytes) trimmed on /dev/nvme0n1\n",
            ""
        ),
        json!({"supported": true, "trimmed_bytes": 1288490188_u64})
    );
    assert_eq!(
        trim_outcome(
            false,
            "",
            "fstrim: /var/lib/lemma-data: the discard operation is not supported"
        ),
        json!({"supported": false, "detail": "the data disk does not accept discards"})
    );
    assert_eq!(trim_outcome(false, "", "boom")["supported"], true);
}

#[test]
fn a_guest_with_no_data_disk_mount_says_so_rather_than_failing() {
    let root = tempdir().unwrap();
    let answer = trim_mount(Path::new("/nonexistent/fstrim"), root.path(), root.path()).unwrap();
    assert_eq!(answer["supported"], false);
}
