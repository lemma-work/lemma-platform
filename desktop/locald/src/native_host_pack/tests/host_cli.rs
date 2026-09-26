//! What a pack tells its backend about the CLI and the sandbox bundle it ships.

use super::*;

fn backend_env(pack: &Path, root: &Path) -> Value {
    let paths = LocalPaths::new(root.join("locald"));
    paths.ensure().unwrap();
    let output = prepare(
        &paths,
        pack,
        ManagedManifestMaterial {
            postgres_password: "a".repeat(64),
            redis_password: "b".repeat(64),
            bridge_executable: PathBuf::from("/signed/lemma-runtime"),
        },
        &mut Vec::new(),
    )
    .unwrap();
    let manifest: Value = serde_json::from_slice(&fs::read(output).unwrap()).unwrap();
    manifest["services"][0]["env"].clone()
}

/// A pack that ships them names them: host commands then run this release's
/// `lemma`, and sandboxes this release's first-party code.
#[test]
fn a_pack_with_a_cli_and_a_bundle_names_both_to_its_backend() {
    let root = tempdir().unwrap();
    let pack = root.path().join("pack");
    fs::create_dir_all(&pack).unwrap();
    fixture(&pack);
    fs::create_dir_all(pack.join("backend/bin")).unwrap();
    fs::write(pack.join("backend/bin/lemma"), b"#!/bin/sh\n").unwrap();
    fs::create_dir_all(pack.join("backend/assets/runtime-bundle")).unwrap();
    fs::write(
        pack.join("backend/assets/runtime-bundle/manifest.json"),
        b"{}",
    )
    .unwrap();

    let env = backend_env(&pack, root.path());

    let cli = PathBuf::from(env["WORKSPACE_HOST_CLI_ROOT"].as_str().unwrap_or_default());
    assert!(cli.join("bin/lemma").is_file(), "{env}");
    assert!(cli.ends_with("backend"), "{env}");
    assert!(
        env["WORKSPACE_RUNTIME_BUNDLE_DIR"]
            .as_str()
            .is_some_and(|path| path.ends_with("backend/assets/runtime-bundle")),
        "{env}"
    );
}

/// A pack from before either shipped starts as it always did.
#[test]
fn a_pack_without_them_names_neither() {
    let root = tempdir().unwrap();
    let pack = root.path().join("pack");
    fs::create_dir_all(&pack).unwrap();
    fixture(&pack);

    let env = backend_env(&pack, root.path());

    assert!(env.get("WORKSPACE_HOST_CLI_ROOT").is_none(), "{env}");
    assert!(env.get("WORKSPACE_RUNTIME_BUNDLE_DIR").is_none(), "{env}");
}
