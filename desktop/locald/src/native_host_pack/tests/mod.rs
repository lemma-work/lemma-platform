//! The host pack's guards, grouped the way the code they cover is grouped.

mod domain;
mod layout;
mod manifest;
mod paths;
mod secrets;

/// Every module of the host pack, without the guards.
///
/// `native_host_pack.rs` was one file, so a scan of "the shipping half" meant
/// everything above its own `#[cfg(test)]`. It is a directory now: the
/// shipping half is every module but this one, and a guard still reading one
/// file would cover a fraction of what it used to.
pub(super) fn pack_source() -> String {
    let source = crate::rust_sources(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/native_host_pack"),
        "native_host_pack",
    )
    .into_iter()
    .filter(|(name, _)| !name.contains("tests"))
    .map(|(_, body)| body)
    .collect::<Vec<_>>()
    .join("\n");
    assert!(
        source.contains("fn canonicalize_for_children("),
        "the scan is not reading the host pack's source"
    );
    source
}

use super::*;
use tempfile::tempdir;

pub(super) fn fixture(root: &Path) {
    for relative in [
        "backend/python/bin/python3",
        "frontend/node/bin/node",
        "frontend/frontend-launcher.mjs",
        "frontend/app/server.js",
        "backend/assets/browser-sdk/lemma-client.js",
        "backend/assets/browser-sdk/lemma-ui.js",
    ] {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, b"fixture").unwrap();
    }
    fs::create_dir_all(root.join("backend/assets/lemma-skills")).unwrap();
    fs::write(
        root.join("pack.json"),
        br#"{"schema_version":1,"release":"6.2.0"}"#,
    )
    .unwrap();
    fs::write(
        root.join("release.json"),
        br#"{
          "schema_version": 1,
          "version": "6.2.0",
          "images": {
            "workspace": {
              "ref": "workspace",
              "digest": "sha256:workspace"
            },
            "function": {
              "ref": "function",
              "digest": "sha256:function"
            }
          },
          "infra": {
            "postgres": {"ref": "postgres", "digest": "sha256:postgres"},
            "redis": "redis@sha256:redis",
            "supertokens": "supertokens@sha256:supertokens"
          }
        }"#,
    )
    .unwrap();
}
