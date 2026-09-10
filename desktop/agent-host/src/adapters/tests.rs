use super::*;

#[test]
#[cfg(unix)]
fn version_discovery_does_not_deadlock_on_full_output_pipes() {
    use std::os::unix::fs::PermissionsExt;
    let directory = tempfile::tempdir().unwrap();
    let executable = directory.path().join("chatty-agent");
    std::fs::write(&executable, "#!/bin/sh\nprintf 'agent 1.2.3\\n'\ni=0\nwhile [ \"$i\" -lt 30000 ]; do printf 'stdout diagnostic\\n'; printf 'stderr diagnostic\\n' >&2; i=$((i+1)); done\n").unwrap();
    std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o700)).unwrap();
    let output = probe_version_within(&executable, &[], Duration::from_secs(5));
    assert!(
        output
            .as_ref()
            .is_ok_and(|text| text.starts_with("agent 1.2.3")),
        "{output:?}"
    );
}

/// A script that reports it started, waits, and reports it finished.
///
/// The second marker is the one that matters: it only ever appears if the
/// process outlived the probe that gave up on it.
#[cfg(unix)]
fn slow_agent(directory: &Path, seconds: u32) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let executable = directory.join("slow-agent");
    std::fs::write(
        &executable,
        format!(
            "#!/bin/sh\ntouch '{0}/started'\nsleep {seconds}\ntouch '{0}/finished'\n",
            directory.display()
        ),
    )
    .unwrap();
    std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o700)).unwrap();
    executable
}

#[test]
#[cfg(unix)]
fn an_agent_that_does_not_answer_in_time_is_reported_as_a_timeout() {
    // These were the same `None`, so a busy laptop and a broken install
    // produced one sentence -- "version could not be determined (minimum
    // 2.1.0)" -- which names the user's agent for something the host did to
    // itself. It sent us reading Claude Code release notes over a probe that
    // simply ran while the adapter cache was still being hashed.
    let directory = tempfile::tempdir().unwrap();
    let executable = slow_agent(directory.path(), 30);

    let outcome = probe_version_within(&executable, &[], Duration::from_millis(200));

    assert_eq!(outcome, Err(VersionUnknown::TimedOut));
}

#[test]
#[cfg(unix)]
fn giving_up_on_a_probe_does_not_leave_the_agent_running() {
    // `return None` dropped the `Child`, and dropping neither reaps nor
    // kills -- so every timeout left an agent running against a question
    // nobody was waiting for an answer to.
    // Three spans, sized off one budget so they cannot eat into each other:
    // the script sleeps twice the budget, so the timeout is always what ends
    // it, and the wait afterwards is a second longer than the sleep the
    // script had left -- which is the window a kill that did not land shows
    // up in.
    //
    // Retried, because one span is not ours to size: the budget also has to
    // cover forking a shell, and under enough load it does not. That is a
    // trial with nothing in it -- the stand-in was killed before its first
    // line, so neither outcome is evidence -- and it used to be an outright
    // failure on `the stand-in agent never ran`. Measured at roughly one run
    // in forty on a saturated machine, which is exactly the rate this was
    // costing CI. The budget grows per attempt, so a slow machine converges
    // on one it can meet instead of retrying at a number it cannot.
    for attempt in 1u32..=4 {
        let directory = tempfile::tempdir().unwrap();
        let budget = Duration::from_millis(1500 * u64::from(attempt));
        let executable = slow_agent(directory.path(), 3 * attempt);

        let outcome = probe_version_within(&executable, &[], budget);
        assert_eq!(outcome, Err(VersionUnknown::TimedOut));

        // Checked, not assumed: "nothing finished" proves nothing about a
        // kill if the script never ran in the first place.
        if !directory.path().join("started").exists() {
            continue;
        }
        std::thread::sleep(budget + Duration::from_secs(1));
        assert!(
            !directory.path().join("finished").exists(),
            "the agent outlived the probe that gave up on it"
        );
        return;
    }
    panic!("the stand-in agent never ran, even given four times the budget");
}

#[test]
fn only_a_probe_that_ran_out_of_time_is_worth_trying_again_soon() {
    // An agent that is not installed fails identically on every refresh. On
    // a machine without Cursor, treating that as worth retrying re-probes it
    // for the life of the process.
    let timed_out = "Claude Code did not answer `--version` within 30s [transient]";
    let missing = "adapter executable cursor-agent was not found";

    assert!(reason_is_transient(timed_out));
    assert!(!reason_is_transient(missing));
    // And the bookkeeping never reaches a reader.
    assert_eq!(
        reason_without_marker(timed_out),
        "Claude Code did not answer `--version` within 30s"
    );
    assert_eq!(reason_without_marker(missing), missing);
}

#[test]
fn an_unreachable_agent_is_not_described_as_a_slow_one() {
    // The published reason is what a person reads in the app, so the marker
    // has to be gone by the time a snapshot carries it.
    let manifest = AdapterManifest::builtin().unwrap();
    let spec = manifest.adapters[0].clone();
    let snapshot = snapshot_unavailable(
        &spec,
        "Claude Code did not answer `--version` within 30s [transient]",
    );

    let reason = snapshot.stale_reason.expect("a reason");
    assert!(reason_is_transient(&reason), "the loop still needs to know");
    assert!(
        !reason_without_marker(&reason).contains("transient"),
        "but nobody should read the bookkeeping: {reason}"
    );
}

#[test]
fn an_adapter_is_resolved_once_and_then_served_from_cache() {
    // Resolving hashes the whole npm package and execs the agent binary to
    // read its version. Paying that per run put ~5s on the poll loop before
    // every message, which was most of the latency users saw.
    let manifest = AdapterManifest::builtin().unwrap();
    let key = manifest.adapters[0].key.clone();

    // A non-npm adapter resolves straight off PATH; if this machine has no
    // such binary there is nothing to cache and nothing to assert.
    let Ok(first) = manifest.resolve(&key) else {
        return;
    };

    assert_eq!(
        manifest.resolved.lock().unwrap().len(),
        1,
        "resolving did not populate the cache"
    );

    let second = manifest.resolve(&key).unwrap();
    assert_eq!(first.command, second.command);
    assert_eq!(first.upstream_version, second.upstream_version);

    // Clones share the cache: the poll loop, the probe task and each run
    // task all hold their own clone of the manifest.
    let cloned = manifest.clone();
    assert_eq!(cloned.resolved.lock().unwrap().len(), 1);
}

#[test]
fn changing_the_cache_root_discards_what_was_resolved_under_the_old_one() {
    let manifest = AdapterManifest::builtin().unwrap();
    let key = manifest.adapters[0].key.clone();
    if manifest.resolve(&key).is_err() {
        return;
    }
    assert_eq!(manifest.resolved.lock().unwrap().len(), 1);

    let moved = manifest.with_cache_root("/nonexistent-adapter-cache");

    assert!(
        moved.resolved.lock().unwrap().is_empty(),
        "a stale resolution survived a cache-root change"
    );
}

#[test]
fn builtin_manifest_is_valid_and_pinned() {
    let manifest = AdapterManifest::builtin().unwrap();
    assert_eq!(manifest.adapters.len(), 4);
    assert!(manifest.adapters.iter().all(|item| {
        item.distribution == "native"
            || item
                .distribution
                .rsplit_once('@')
                .is_some_and(|(_, version)| semver::Version::parse(version).is_ok())
    }));
}

#[test]
fn every_npm_adapter_is_told_which_agent_to_run() {
    // The bug this exists to stop coming back: an npm adapter that is not
    // told where the agent is picks one for itself, and `claude-agent-acp`
    // picks a copy vendored inside its own package. Lemma then probed the
    // agent on this machine, published *that* version, and ran a different
    // binary carrying none of the user's configuration.
    //
    // Native adapters are exempt: they are the agent, so there is nothing to
    // point them at.
    let manifest = AdapterManifest::builtin().unwrap();
    for adapter in &manifest.adapters {
        if adapter.distribution.starts_with("npm:") {
            assert!(
                adapter
                    .upstream_path_env
                    .as_deref()
                    .is_some_and(|name| !name.trim().is_empty()),
                "npm adapter {} must name the variable that points at the agent",
                adapter.key
            );
            // And the other half of the same decision: an adapter told where
            // the agent is has no use for the vendored copy in its optional
            // dependencies, which is 548 MB across the two certified ones.
            assert!(
                adapter.omit_optional_dependencies,
                "npm adapter {} points at the real agent, so it must not also fetch a vendored one",
                adapter.key
            );
        } else {
            // Native adapters install nothing, so the flag would describe an
            // install that never happens.
            assert!(
                !adapter.omit_optional_dependencies,
                "native adapter {} has no npm install to omit anything from",
                adapter.key
            );
        }
    }
}

#[test]
fn the_adapter_environment_names_the_upstream_binary() {
    let manifest = AdapterManifest::builtin().unwrap();
    let spec = manifest
        .adapters
        .iter()
        .find(|adapter| adapter.key == "claude-code")
        .expect("claude-code is a certified adapter")
        .clone();
    let variable = spec
        .upstream_path_env
        .clone()
        .expect("claude-code names its variable");
    let resolved = ResolvedAdapter {
        spec,
        command: PathBuf::from("/cache/claude-agent-acp"),
        upstream_command: PathBuf::from("/usr/local/bin/claude"),
        upstream_version: Some("2.1.0".to_owned()),
    };

    let environment = resolved.environment();
    assert_eq!(
        environment.get(&variable).map(String::as_str),
        Some("/usr/local/bin/claude"),
        "the adapter must be pointed at the agent the host actually probed"
    );
    // PATH still leads with the agent's directory; the variable is belt and
    // braces for adapters that never consult it.
    assert!(
        environment
            .get("PATH")
            .is_some_and(|path| path.starts_with("/cache")),
        "the adapter and agent directories still lead PATH"
    );
}

#[test]
fn the_installed_fingerprint_is_stable_and_covers_every_adapter() {
    // Stability is the whole point: an unstable fingerprint would re-probe
    // every couple of seconds forever, which is worse than the fifteen
    // minute timer it replaces.
    let manifest = AdapterManifest::builtin().unwrap();
    assert_eq!(
        manifest.installed_fingerprint(),
        manifest.installed_fingerprint()
    );
    assert_eq!(manifest.installed_fingerprint().len(), 64);

    // And it has to distinguish machines, or nothing is ever detected.
    //
    // Demonstrated through the adapter cache, which this test owns, rather
    // than by renaming `upstream_command` to something that cannot resolve.
    // That version passed only on a machine with an agent installed: where
    // none is, the real manifest and the renamed one both resolve every
    // command to "absent" and fingerprint identically. It was green
    // everywhere a developer ran it and red on every CI runner, which is the
    // worst way round.
    let cache = tempfile::tempdir().unwrap();
    let manifest = manifest.with_cache_root(cache.path().to_path_buf());
    let spec = manifest
        .adapters
        .iter()
        .find(|adapter| adapter.distribution.starts_with("npm:"))
        .expect("a certified npm adapter")
        .clone();

    let before = manifest.installed_fingerprint();
    let executable = cached_adapter_executable(cache.path(), &spec);
    std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
    std::fs::write(&executable, b"#!/bin/sh\n").unwrap();
    let after = manifest.installed_fingerprint();
    assert_ne!(
        before, after,
        "a different set of installed agents must fingerprint differently"
    );

    // Size is folded in, so an agent replaced in place counts as a change
    // even at the same path. Without it an upgrade would be invisible until
    // the fifteen-minute sweep.
    std::fs::write(&executable, b"#!/bin/sh\necho a bigger one\n").unwrap();
    assert_ne!(
        after,
        manifest.installed_fingerprint(),
        "an agent replaced in place must fingerprint differently"
    );
}

#[test]
fn an_adapter_landing_in_the_cache_counts_as_a_change() {
    // The bug: warming the cache in the background lets a probe run first,
    // so the harness publishes as Installing — correctly — and then the
    // install completes and nothing re-probes, because a fingerprint over
    // the *agent* binaries alone cannot see an *adapter* arrive. It stayed
    // "Installing" until the fifteen-minute sweep, which is the wait the
    // fingerprint exists to remove.
    let cache = tempfile::tempdir().unwrap();
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(cache.path().to_path_buf());
    let empty = manifest.installed_fingerprint();

    let spec = manifest
        .adapters
        .iter()
        .find(|adapter| adapter.distribution.starts_with("npm:"))
        .expect("a certified npm adapter");
    let executable = cached_adapter_executable(cache.path(), spec);
    std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
    std::fs::write(&executable, b"#!/bin/sh\n").unwrap();

    assert_ne!(
        empty,
        manifest.installed_fingerprint(),
        "an adapter appearing in the cache must trigger a re-probe"
    );
}

#[test]
fn an_install_that_cannot_succeed_stops_reporting_as_one_in_progress() {
    // "Setting up · usually under a minute" was every missing cache
    // directory, whether the download was in flight or impossible. Warming
    // is detached and best-effort by design, so its failure was a log line
    // and nothing the user could see -- and a machine behind a proxy that
    // blocks the npm registry read as permanently one minute from ready.
    let cache = tempfile::tempdir().unwrap();
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(cache.path().to_path_buf());
    let spec = manifest
        .adapters
        .iter()
        .find(|adapter| adapter.distribution.starts_with("npm:"))
        .expect("a certified npm adapter")
        .clone();

    // Nothing has been tried yet: not there is not the same as broken.
    let waiting = manifest.snapshot_for(&spec);
    assert_eq!(waiting.health, HarnessHealth::Installing);
    assert!(waiting.stale_reason.is_none());
    let before_failure = manifest.installed_fingerprint();

    manifest.install_failures.lock().unwrap().insert(
        spec.key.clone(),
        "npm is required to install ACP adapters".into(),
    );

    let failed = manifest.snapshot_for(&spec);
    assert_ne!(before_failure, manifest.installed_fingerprint());
    assert_eq!(
        failed.health,
        HarnessHealth::ProbeFailed,
        "an install that already failed is not an install in progress"
    );
    assert!(
        failed
            .stale_reason
            .as_deref()
            .is_some_and(|reason| reason.contains("npm is required")),
        "and it has to name the cause: {:?}",
        failed.stale_reason
    );

    // A later success clears it, so `doctor --repair` is a real remedy
    // rather than something that leaves the row saying it failed.
    manifest.install_failures.lock().unwrap().remove(&spec.key);
    assert_eq!(before_failure, manifest.installed_fingerprint());
    assert_eq!(
        manifest.snapshot_for(&spec).health,
        HarnessHealth::Installing
    );
}

#[test]
fn manifest_digest_is_stable() {
    let manifest = AdapterManifest::builtin().unwrap();
    assert_eq!(manifest.content_digest(), manifest.content_digest());
    assert_eq!(manifest.content_digest().len(), 64);
}

#[test]
fn provider_version_output_is_compared_without_guessing() {
    assert!(version_is_at_least("claude 2.1.220", "2.1.0"));
    assert!(version_is_at_least("2026.07.09-a3815c0", "2026.3.11"));
    assert!(!version_is_at_least("opencode 1.16.9", "1.17.0"));
    assert!(!version_is_at_least("development build", "1.0.0"));
}

#[test]
fn executable_resolution_uses_explicit_search_paths() {
    let root = tempfile::tempdir().unwrap();
    let executable = root.path().join("codex");
    std::fs::write(&executable, b"fixture").unwrap();
    assert_eq!(
        resolve_executable_in("codex", [root.path().to_path_buf()]),
        Some(executable)
    );
}

#[test]
fn detection_covers_the_directories_these_agents_install_themselves_into() {
    // The Agent Host is a sidecar of a GUI app, so it inherits
    // /usr/bin:/bin:/usr/sbin:/sbin and nothing else — not a login shell's
    // PATH. Every agent people actually run is therefore invisible unless
    // this list names where it lives, and the ones shipping their own
    // installer are easiest to miss: OpenCode was reported as "adapter
    // executable opencode was not found" on a machine that had it at
    // ~/.opencode/bin/opencode.
    let home = Path::new("/home/example");
    let directories = home_executable_directories(home);

    for expected in [".opencode/bin", ".claude/local", ".local/bin"] {
        assert!(
            directories.contains(&home.join(expected)),
            "{expected} must be searched; it is where an agent's own installer puts it",
        );
    }
}

#[test]
fn cache_integrity_covers_files_beyond_the_executable() {
    let root = tempfile::tempdir().unwrap();
    let executable = root.path().join("node_modules/.bin/codex-acp");
    let implementation = root.path().join("node_modules/codex-acp/dist/index.js");
    std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
    std::fs::create_dir_all(implementation.parent().unwrap()).unwrap();
    std::fs::write(&executable, b"#!/usr/bin/env node\n").unwrap();
    std::fs::write(&implementation, b"export const safe = true;\n").unwrap();
    let digest = directory_sha256(root.path()).unwrap();
    std::fs::write(root.path().join(".lemma-cache.sha256"), digest).unwrap();
    verify_cached_adapter(&executable).unwrap();

    std::fs::write(&implementation, b"export const safe = false;\n").unwrap();
    assert!(
        verify_cached_adapter(&executable)
            .unwrap_err()
            .to_string()
            .contains("failed integrity validation")
    );
}

#[test]
fn nvm_search_prefers_the_newest_installed_node() {
    let root = tempfile::tempdir().unwrap();
    for version in ["v18.20.1", "v22.14.0", "not-a-version"] {
        std::fs::create_dir_all(root.path().join(".nvm/versions/node").join(version)).unwrap();
    }
    let paths = nvm_node_bins(root.path());
    assert!(paths[0].ends_with("v22.14.0/bin"));
    assert!(paths[1].ends_with("v18.20.1/bin"));
    assert_eq!(paths.len(), 2);
}
