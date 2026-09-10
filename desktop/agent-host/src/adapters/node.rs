//! Whether the Node on this computer can run the adapter that was installed.
//!
//! Every npm adapter states the Node it needs in its own `package.json`, and
//! `npm install` does not enforce it: an unmet `engines` field is `EBADENGINE`,
//! a *warning*, and the install succeeds. So on a computer with an old Node the
//! adapter is fetched, verified, cached and launched, and the first thing the
//! user sees is a parse error from a file inside `node_modules` -- naming a line
//! of somebody else's code, saying nothing about Node, on a machine whose owner
//! has no reason to suspect their Node at all.
//!
//! The floor is read from the adapter rather than written down here. Pinning a
//! number in Lemma would be a second claim about the same thing, free to drift
//! from the one the adapter actually enforces at parse time.

use std::path::Path;

use super::{
    AdapterSpec, adapter_search_paths, cached_adapter_directory, probe_version,
    resolve_executable_in,
};

/// The Node range an adapter declares, if it declares one.
pub(crate) fn declared_node_range(package_json: &str) -> Option<String> {
    let manifest: serde_json::Value = serde_json::from_str(package_json).ok()?;
    let range = manifest.get("engines")?.get("node")?.as_str()?.trim();
    (!range.is_empty()).then(|| range.to_owned())
}

/// Whether `range` definitively rules `version` out.
///
/// False whenever we cannot tell. npm's range syntax is wider than this
/// parser's -- `||` alternatives above all -- and a floor nobody can read must
/// not become a new way for a working installation to be refused. The check
/// exists to turn one silent failure into a sentence, not to add one.
pub(crate) fn node_is_excluded(range: &str, version: &str) -> bool {
    // npm separates an `AND` with a space; this parser separates it with a
    // comma. Everything else about the two agrees.
    let normalized = range.split_whitespace().collect::<Vec<_>>().join(",");
    let Ok(requirement) = semver::VersionReq::parse(&normalized) else {
        return false;
    };
    let Ok(version) = semver::Version::parse(version.trim().trim_start_matches('v')) else {
        return false;
    };
    // Prereleases are matched on the release they precede: a `24.0.0-nightly`
    // is a 24 for the purpose of a floor, and `VersionReq` alone would say no
    // to every one of them.
    let release = semver::Version::new(version.major, version.minor, version.patch);
    !requirement.matches(&release)
}

/// Refuse an adapter this computer's Node cannot run, and say why.
///
/// `node_version` is passed in rather than probed so the decision is a
/// function of what was found. The caller is the one that knows which `node`
/// the adapter's own shim will pick, and probing twice could disagree.
pub(crate) fn ensure_node_runs(
    package_root: &Path,
    adapter: &str,
    node_version: &str,
) -> anyhow::Result<()> {
    let manifest = package_root.join("package.json");
    let Ok(text) = std::fs::read_to_string(&manifest) else {
        return Ok(());
    };
    let Some(range) = declared_node_range(&text) else {
        return Ok(());
    };
    anyhow::ensure!(
        !node_is_excluded(&range, node_version),
        "{adapter} needs Node {range} and this computer has Node {}. \
         Install a newer Node, then reopen Lemma.",
        node_version.trim()
    );
    Ok(())
}

/// The package an `npm:` distribution pins, without its version.
pub(crate) fn pinned_package_name(distribution: &str) -> Option<&str> {
    distribution
        .strip_prefix("npm:")?
        .rsplit_once('@')
        .map(|(name, _)| name)
        .filter(|name| !name.is_empty())
}

/// Check the Node that will launch this adapter against what it declares.
///
/// Every step here that cannot answer returns `Ok`. There is no Node on the
/// search path, or it will not say its version, or the adapter declares
/// nothing: in each case the shim is about to run and this check has nothing to
/// add. It only ever converts a failure that would have happened anyway into
/// one that says what to do about it.
pub(crate) fn ensure_adapter_node_runs(
    spec: &AdapterSpec,
    cache_root: &Path,
    command: &Path,
    upstream_command: &Path,
) -> anyhow::Result<()> {
    ensure_adapter_node_runs_with(spec, cache_root, || {
        node_version_on_this_computer(command, upstream_command)
    })
}

/// The Node the adapter's own shim will pick, and what it calls itself.
///
/// Resolved through `adapter_search_paths`, which is what
/// `ResolvedAdapter::environment` puts in the child's `PATH` -- so this is the
/// Node the shim's `#!/usr/bin/env node` will find. Searching only
/// `executable_search_paths()` would have missed the two directories that
/// environment prepends, which are precisely where a second Node would shadow
/// the one on the ordinary path.
fn node_version_on_this_computer(command: &Path, upstream_command: &Path) -> Option<String> {
    let node = resolve_executable_in("node", adapter_search_paths(command, upstream_command))?;
    probe_version(&node, &["--version".to_owned()]).ok()
}

/// The body, with the lookup passed in.
///
/// A seam rather than a probe, so the wiring -- which cached directory holds
/// the manifest, and which name goes in the message -- is testable without a
/// particular Node installed and without writing to this process's
/// environment, which the rest of the suite is reading in parallel.
pub(crate) fn ensure_adapter_node_runs_with(
    spec: &AdapterSpec,
    cache_root: &Path,
    node_version: impl FnOnce() -> Option<String>,
) -> anyhow::Result<()> {
    let Some(package) = pinned_package_name(&spec.distribution) else {
        return Ok(());
    };
    let root = cached_adapter_directory(cache_root, spec)
        .join("node_modules")
        .join(package);
    let Some(reported) = node_version() else {
        return Ok(());
    };
    ensure_node_runs(&root, &spec.command, &reported)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_declared_floor_is_read_and_an_absent_one_is_not_invented() {
        assert_eq!(
            declared_node_range(r#"{"engines":{"node":">=20"}}"#).as_deref(),
            Some(">=20")
        );
        for absent in [
            r#"{"engines":{}}"#,
            r#"{"engines":{"node":"  "}}"#,
            r#"{"engines":{"node":20}}"#,
            r#"{"name":"x"}"#,
            "not json at all",
        ] {
            assert_eq!(declared_node_range(absent), None, "{absent}");
        }
    }

    /// The case this exists for: npm warns, installs anyway, and the adapter
    /// fails later with a syntax error.
    #[test]
    fn a_node_below_the_declared_floor_is_ruled_out() {
        assert!(node_is_excluded(">=20", "v18.20.4"));
        assert!(node_is_excluded(">=20.6.0", "v20.5.1"));
        assert!(node_is_excluded(">=18 <22", "v24.1.0"));
        assert!(node_is_excluded("^20.0.0", "v18.0.0"));
    }

    #[test]
    fn a_node_that_satisfies_the_floor_is_left_alone() {
        assert!(!node_is_excluded(">=20", "v22.11.0"));
        assert!(!node_is_excluded(">=18 <22", "v20.0.0"));
        assert!(!node_is_excluded("*", "v18.0.0"));
        // A nightly or release candidate is the version it precedes.
        assert!(!node_is_excluded(">=20", "v24.0.0-nightly20260101"));
    }

    /// A range this parser does not understand must let the install through.
    /// Refusing what we cannot read would replace a confusing failure with a
    /// confident wrong one.
    #[test]
    fn a_range_we_cannot_read_never_refuses() {
        for range in ["^18 || ^20", "18 - 22", "lts/*", "", "  "] {
            assert!(!node_is_excluded(range, "v16.0.0"), "{range}");
        }
        assert!(!node_is_excluded(">=20", "not-a-version"));
    }

    /// The Node this checks is the Node the shim will run.
    ///
    /// `ResolvedAdapter::environment` puts the adapter's own directory and the
    /// agent's in front of everything else, so a `node` sitting in either of
    /// them is the one `#!/usr/bin/env node` finds. The probe used to search
    /// only the ordinary path and would have reported a different version than
    /// the one that runs -- which is worse than not checking, because the
    /// answer looks authoritative.
    #[cfg(unix)]
    #[test]
    fn the_probe_searches_the_path_the_adapter_will_run_under() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let beside_the_adapter = root.path().join("cache/node_modules/.bin");
        std::fs::create_dir_all(&beside_the_adapter).unwrap();
        let shadowing_node = beside_the_adapter.join("node");
        std::fs::write(&shadowing_node, "#!/bin/sh\necho v22.0.0\n").unwrap();
        std::fs::set_permissions(&shadowing_node, std::fs::Permissions::from_mode(0o755)).unwrap();

        let command = beside_the_adapter.join("codex-acp");
        let upstream = root.path().join("agent/codex");

        let found = super::adapter_search_paths(&command, &upstream)
            .into_iter()
            .find(|directory| directory.join("node").is_file());

        assert_eq!(
            found.as_deref(),
            Some(beside_the_adapter.as_path()),
            "the adapter's own directory has to come before the ordinary path, \
             or the version reported is not the version that runs",
        );
    }

    #[test]
    fn a_pinned_distribution_yields_its_package_name() {
        assert_eq!(
            pinned_package_name("npm:@agentclientprotocol/codex-acp@1.1.7"),
            Some("@agentclientprotocol/codex-acp")
        );
        for other in ["native", "npm:@scope/name", "npm:@1.0.0", "npm:"] {
            assert_eq!(pinned_package_name(other), None, "{other}");
        }
    }

    /// End to end over a real cache layout: the manifest is read from the
    /// pinned package's own directory inside the cache, which is the part of
    /// this that a wrong join would break silently -- an unreadable manifest
    /// declares nothing, and nothing is exactly what a passing check looks
    /// like.
    #[test]
    fn the_declared_floor_is_read_from_the_cached_package_itself() {
        let cache = tempfile::tempdir().unwrap();
        let spec = spec_for("npm:@agentclientprotocol/codex-acp@1.1.7");
        let package = cached_adapter_directory(cache.path(), &spec)
            .join("node_modules/@agentclientprotocol/codex-acp");
        std::fs::create_dir_all(&package).unwrap();
        std::fs::write(
            package.join("package.json"),
            r#"{"engines":{"node":">=20"}}"#,
        )
        .unwrap();

        let error =
            ensure_adapter_node_runs_with(&spec, cache.path(), || Some("v18.20.4".to_owned()))
                .unwrap_err()
                .to_string();
        assert!(error.contains("codex-acp"), "{error}");
        assert!(error.contains(">=20"), "{error}");

        ensure_adapter_node_runs_with(&spec, cache.path(), || Some("v22.0.0".to_owned())).unwrap();
        // No Node to ask: the shim is about to fail on its own, and guessing
        // here would only replace its message with a wrong one.
        ensure_adapter_node_runs_with(&spec, cache.path(), || None).unwrap();
        // Not an npm adapter, so there is no package manifest to consult.
        ensure_adapter_node_runs_with(&spec_for("native"), cache.path(), || {
            Some("v18.20.4".to_owned())
        })
        .unwrap();
    }

    fn spec_for(distribution: &str) -> AdapterSpec {
        AdapterSpec {
            key: "codex".to_owned(),
            display_name: "Codex".to_owned(),
            adapter_version: "1.1.7".to_owned(),
            command: "codex-acp".to_owned(),
            args: Vec::new(),
            upstream_command: "codex".to_owned(),
            upstream_version_args: vec!["--version".to_owned()],
            upstream_path_env: None,
            omit_optional_dependencies: true,
            minimum_upstream_version: None,
            distribution: distribution.to_owned(),
            artifact_integrity: None,
            license: "Apache-2.0".to_owned(),
        }
    }

    #[test]
    fn the_refusal_names_both_versions_and_a_missing_manifest_is_not_one() {
        let root = tempfile::tempdir().unwrap();
        // No package.json: nothing is claimed, so nothing is refused.
        ensure_node_runs(root.path(), "codex-acp", "v18.0.0").unwrap();

        std::fs::write(
            root.path().join("package.json"),
            r#"{"engines":{"node":">=20"}}"#,
        )
        .unwrap();
        let error = ensure_node_runs(root.path(), "codex-acp", "v18.20.4")
            .unwrap_err()
            .to_string();
        assert!(error.contains("codex-acp"), "{error}");
        assert!(error.contains(">=20"), "{error}");
        assert!(error.contains("v18.20.4"), "{error}");

        ensure_node_runs(root.path(), "codex-acp", "v22.0.0").unwrap();
    }
}
