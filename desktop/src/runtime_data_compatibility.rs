//! Whether a release can take over this installation's data.
//!
//! One question, asked in two directions. The release feed says which Postgres
//! major it ships; this says which one the installed runtime runs. They are
//! compared before anything is downloaded, and an update that cannot preserve
//! the data is refused rather than attempted.
//!
//! Its own module because the whole of it went missing once and nobody could
//! see that it had: `dataCompatibility` was read in one place and written in
//! none, so the installed side of the comparison was `None` for every
//! installation there has ever been, the answer was always "unknown", and the
//! in-app updater was closed to every user who had installed Local Lemma.
//! Gathered here so "what do we know about this installation's data" is a file
//! somebody can open rather than a pointer buried in a setup routine.

use super::*;

/// The Postgres major an installed runtime runs, read from its own manifest.
///
/// `infra.postgres` is a pinned image reference and the major is in its tag --
/// `pgvector/pgvector:0.8.3-pg18`. The release feed derives its
/// `lemma.postgres_major` from exactly the same field of exactly the same
/// manifest (see `release-desktop.yml`), so the two sides of the compatibility
/// comparison are the same number read the same way.
///
/// Both shapes are accepted because both are real: the published release
/// manifest carries `infra.postgres` as a string, and the one inside an
/// installed host pack carries `{"ref": ..., "digest": ...}`. `pull_ref` in
/// locald exists for the same reason and is where that divergence is handled
/// for every other image.
pub(crate) fn runtime_postgres_major(host_pack_root: &std::path::Path) -> Option<u64> {
    let manifest: Value = std::fs::read_to_string(host_pack_root.join("release.json"))
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())?;
    let reference = match manifest.pointer("/infra/postgres")? {
        Value::String(value) => value.clone(),
        Value::Object(value) => value.get("ref")?.as_str()?.to_owned(),
        _ => return None,
    };
    postgres_major_of(&reference)
}

/// The major in a pinned Postgres image reference, or None if it names none.
///
/// Split out so it can be tested against the real strings rather than only
/// through a manifest on disk.
pub(crate) fn postgres_major_of(reference: &str) -> Option<u64> {
    let tag = reference.rsplit_once("-pg")?.1;
    let digits: String = tag.chars().take_while(char::is_ascii_digit).collect();
    digits.parse().ok()
}

/// The Postgres major this installation's data was created with.
///
/// Recorded on the config when a runtime is activated, and otherwise derived
/// from the installed runtime's own manifest -- which is what makes this
/// answerable at all for an installation that predates the recording.
///
/// **Nothing used to write the recorded value.** `dataCompatibility` was read
/// here and set nowhere, on any platform, in any code path, so this returned
/// `None` for every installation that has ever existed. `compatibility_with`
/// maps `None` to `"unknown"`, and `ensure_update_preserves_data` refuses
/// anything that is not `"compatible"` while local runtime data exists -- so
/// the in-app updater was closed to every user who had actually installed
/// Local Lemma, which is all of them. The tests passed because they built
/// `LemmaUpdateMetadata` with a literal `Some(18)` and never asked where the
/// installed side came from.
pub(crate) fn installed_postgres_major() -> Option<u64> {
    postgres_major_from_config(&read_config())
}

/// The same answer, from a config value rather than from disk.
///
/// Split so the wiring can be tested. The two helpers above are pure and were
/// easy to test in isolation, and testing only those is how the original bug
/// survived: a function nothing calls passes every test written about it.
///
/// Deliberately not routed through `configured_runtime`, which also checks the
/// runtime's markers are intact. An installation whose runtime is incomplete
/// still has a database and still needs an answer here -- refusing to derive
/// one would block the update of the installation most likely to need it, and
/// the manifest this reads is the only thing the answer actually depends on.
pub(crate) fn postgres_major_from_config(config: &Value) -> Option<u64> {
    if let Some(recorded) = config
        .pointer("/installedRuntime/dataCompatibility/postgres_major")
        .and_then(Value::as_u64)
    {
        return Some(recorded);
    }
    let root = config
        .pointer("/installedRuntime/root")
        .and_then(Value::as_str)?;
    let release = config
        .pointer("/installedRuntime/release")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let runtime = artifact_install::installed_runtime(std::path::Path::new(root), release);
    runtime_postgres_major(&runtime.host_pack_root)
}
