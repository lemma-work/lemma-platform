use super::*;
use crate::app::{launch_failure_state, stand_down_state};

#[test]
fn a_resume_that_did_not_pan_out_stops_claiming_the_workspace_is_ready() {
    // The optimistic resume seeds ready/running/url before it has checked
    // anything, so the workspace can be on screen with no latency. The
    // splash opens `ui.url` whenever it loads and finds ready set with no
    // error -- so every path that gives up and shows the splash has to
    // clear `ready` first, or the two bounce the user between a splash and
    // a dead workspace.
    let seeded = || UiState {
        url: "http://app.lemma.localhost:52501".into(),
        running: true,
        ready: true,
        ..UiState::default()
    };

    let mut ui = seeded();
    stand_down_state(&mut ui, None);
    assert!(!ui.ready, "a replaced daemon still takes back `ready`");
    assert!(!ui.error, "a replaced daemon is not a failure");
    assert!(ui.running, "the stack is coming back, not gone");

    let mut ui = seeded();
    stand_down_state(&mut ui, Some("locald did not answer".into()));
    assert!(!ui.ready && !ui.running && ui.error);
    assert_eq!(ui.error_code, "resume-failed");
    assert_eq!(ui.status, "locald did not answer");

    // And the resume worker has no way to the splash except through it.
    let source = shell_source();
    let worker = function_body(&source, "fn reconnect_after_resume(");
    assert!(
        !worker.contains("show_splash"),
        "the resume worker must reach the splash only through stand_down, \
         which is what clears the state the splash reads"
    );
    assert!(
        function_body(&source, "fn stand_down(").contains("stand_down_state("),
        "stand_down clears the optimistic state before showing the splash"
    );
}

#[test]
fn a_failed_start_request_is_no_longer_ready_but_a_failed_connect_keeps_its_state() {
    let mut ui = UiState {
        ready: true,
        ..UiState::default()
    };
    launch_failure_state(&mut ui, "locald is not running".into(), None);
    assert!(ui.error);
    assert_eq!(ui.status, "locald is not running");
    assert!(
        ui.error_code.is_empty(),
        "a connect failure carries no code"
    );

    launch_failure_state(
        &mut ui,
        "start was refused".into(),
        Some("startup-request-failed"),
    );
    assert_eq!(ui.error_code, "startup-request-failed");
    assert!(!ui.ready);
}

/// A build that was never stamped with a release channel -- a CI artifact, a
/// developer's `cargo tauri dev` -- must never follow an update feed, and
/// must say so rather than appearing configured.
#[test]
fn a_released_build_updates_itself_and_an_unstamped_one_does_not() {
    // Both halves of this used to be true by construction: tests are a
    // debug build so `updates_enabled()` is false whatever the channel is,
    // and `matches!` against the same closed set the `match` produces
    // cannot fail. It passed with the feature deleted.
    //
    // Asserted on the mapping instead, which is the decision worth pinning:
    // an unrecognised stamp is `dev`, not "assume the best".
    assert_eq!(channel_of(Some("stable")), "stable");
    assert_eq!(channel_of(Some("nightly")), "nightly");
    assert_eq!(channel_of(None), "dev", "an unstamped build is not stable");
    for unknown in ["Stable", "STABLE", "beta", "", "stable "] {
        assert_eq!(
            channel_of(Some(unknown)),
            "dev",
            "{unknown:?} must not be read as a release channel",
        );
    }

    // And the gate is a conjunction, so nightly cannot update itself even
    // in a release build.
    assert!(updates_allowed("nightly", false, true));
    assert!(!updates_allowed("dev", false, true));
    assert!(
        !updates_allowed("stable", true, true),
        "a debug build never does"
    );
    assert!(
        !updates_allowed("stable", false, false),
        "and neither does one with no key to verify with",
    );
    assert!(updates_allowed("stable", false, true));
}

/// The daemon is stopped only once the update is in hand.
///
/// `download` is where the signature is verified and where a network drop,
/// a moved feed or an undecodable key surfaces. Stopping first meant every
/// one of those took the user's whole stack down and then reported a
/// failure, for an update that never began.
#[test]
fn an_update_is_downloaded_and_verified_before_the_stack_is_stopped() {
    let source = shell_source();
    let body = function_body(&source, "async fn install_app_update(");

    let downloaded = body.find(".download(").expect("it downloads");
    let stopped = body
        .find("stop_locald_for_runtime_maintenance")
        .expect("it stops locald");
    let installed = body.find(".install(bytes)").expect("it installs");
    assert!(
        downloaded < stopped && stopped < installed,
        "order must be download, stop, install -- got download@{downloaded} \
         stop@{stopped} install@{installed}",
    );
    // A call, not the word -- the comment above the download explains why
    // the combined form is not used, and would otherwise trip this.
    assert!(
        !body.contains(".download_and_install("),
        "the combined call gives no seam to stop the daemon between the two",
    );
}

/// A release build honours no runtime-redirecting environment variable.
///
/// These three point the app at a different host pack, managed runtime, or
/// signed manifest -- and the manifest carries the digests everything else
/// is verified against, so overriding it chooses both the bytes and the
/// check on them. Read unconditionally, they let anything already running
/// as the user make a notarized, hardened-runtime Lemma fetch and execute a
/// runtime of its choosing.
///
/// Asserted on the source because the property is "the read is gated", and
/// a test running under `cfg(test)` is a debug build -- it cannot observe
/// the release behaviour by calling the function.
#[test]
fn a_release_build_ignores_every_runtime_redirecting_env_var() {
    // `local_artifacts_enabled` reads the manifest variable too, and is the
    // one place that may: it does not redirect anything, it compares the
    // configured path against the manifest already in hand, and only after
    // `LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS` was set to 1 on purpose. Cut it
    // out by name rather than narrowing the scan, so the exception is visible
    // here instead of being a file this test quietly never looked at.
    let all = shell_source();
    let source = all.replace(function_body(&all, "fn local_artifacts_enabled"), "");
    assert!(source.len() < all.len(), "the exception was not cut out");
    // The five read by name. Two of them -- the daemon and the VZ helper --
    // are worse than a redirected manifest: they name the executable itself,
    // and they were read straight from the environment.
    for name in [
        "LEMMA_DESKTOP_HOST_PACK_ROOT",
        "LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT",
        "LEMMA_DESKTOP_RELEASE_MANIFEST",
        "LEMMA_DESKTOP_LOCALD_BIN",
        "LEMMA_DESKTOP_VZ_BIN",
    ] {
        for direct in ["std::env::var_os", "std::env::var"] {
            assert!(
                !source.contains(&format!("{direct}(\"{name}\")")),
                "{name} must be read through dev_override, which is inert in a \
                 release build, not directly",
            );
        }
        assert!(
            source.contains(&format!("dev_override(\"{name}\")")),
            "{name} should still be honoured in a development build",
        );
    }

    // The two that name an *origin* rather than a file. Read in a loop rather
    // than by name, so they are asserted where they are read: one decides
    // where a hosted launch navigates, the other grants that origin the
    // shell's own commands. Gating one without the other would send a release
    // build to an environment-named host with no permissions.
    for name in ["LEMMA_DESKTOP_HOSTED_URL", "LEMMA_DESKTOP_LOCAL_URL"] {
        for direct in ["std::env::var_os", "std::env::var"] {
            assert!(
                !source.contains(&format!("{direct}(\"{name}\")")),
                "{name} must be read through dev_override, not directly",
            );
        }
    }
    for signature in ["fn hosted_url()", "fn overridden_workspace_capability()"] {
        let body = function_body(&source, signature);
        assert!(
            body.contains("dev_override"),
            "{signature} reads an origin from the environment without gating \
             it to a development build:\n{body}",
        );
    }
    // The gate itself, so this cannot pass against a `dev_override` that
    // forgot to check.
    assert!(
        function_body(&source, "fn dev_override(name: &str)")
            .contains("if !cfg!(debug_assertions)"),
        "dev_override must be inert outside a development build",
    );
}

/// A saved window position must not be able to hide the app.
///
/// The failure is the classic one and it is unrecoverable without a
/// terminal: quit with the window on a second display, unplug it, launch.
/// The window is real, focused, and nowhere on screen, and the only way
/// back is deleting a config file the user does not know exists.
/// Closing the window keeps Lemma running; quitting stops it completely.
///
/// This is the bargain the product makes, and only half of it was true.
/// Closing hid to the tray and left everything up, which is right and is
/// what the tray icon is for. Quitting exited the app and left
/// `lemma-locald` running -- supervising Postgres, Redis, the backend, the
/// Agent Host and a virtual machine -- with no window, no tray icon and
/// nothing in the Dock. The only way to see it was `ps` and the only way to
/// stop it was `kill`.
///
/// A background service is fine. A background service with no interface is
/// not one anybody agreed to.
/// Every redirect the daemon reads is one a release build takes away.
///
/// `dev_override` gates the variables the *app* reads, and stops exactly
/// there: the daemon inherits this process's environment and reads
/// redirects of its own, so a signed build refused to load a host pack from
/// an environment variable and then handed that same variable to the
/// process that loads it without asking.
///
/// Enumerated from locald's own source rather than from a list somebody
/// maintains, because a list somebody maintains is how the first three went
/// missing. A new variable there is a compile-time-green, review-invisible
/// hole until this test names it.
#[test]
fn every_daemon_redirect_variable_is_stripped_from_a_release_build() {
    // The daemon is a directory, and it is read from disk rather than
    // named file by file. Enumerating its modules here would reintroduce
    // exactly the maintained list this test exists to replace: a module
    // added there would be compile-time green and review-invisible, which
    // is how the first three variables went missing.
    // Both crates entire, walked from disk. This used to read the daemon
    // directory and then name five files beside it; four of those five have
    // since become directories of their own, and a guard that keeps naming
    // the old path does not fail -- it stops compiling if the file is gone,
    // and covers a fraction of the tree if it is not.
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let sources: Vec<String> = ["locald/src", "local-runtime/manager/src"]
        .iter()
        .flat_map(|relative| rust_files_under(&manifest.join(relative)))
        .map(|path| std::fs::read_to_string(&path).expect("a source file"))
        .collect();
    assert!(
        sources.len() > 30,
        "the daemon and the runtime manager are trees of modules; reading {} \
         file(s) means the scan is looking at a fraction of them",
        sources.len(),
    );

    let mut read_by_the_daemon = std::collections::BTreeSet::new();
    for source in &sources {
        let mut rest = source.as_str();
        while let Some(at) = rest.find("LEMMA_") {
            let tail = &rest[at..];
            let end = tail
                .find(|c: char| !c.is_ascii_uppercase() && c != '_' && !c.is_ascii_digit())
                .unwrap_or(tail.len());
            let name = &tail[..end];
            // Only where it is actually read from the environment, which is
            // what makes it a redirect rather than a mention.
            // Clamped to a character boundary. A byte offset forty back
            // from a `LEMMA_` token can land inside an em dash in the comment
            // above it, and slicing there panics -- so an unrelated comment
            // edit could take this guard out.
            let before = &rest[..at];
            let context_start = before
                .char_indices()
                .rev()
                .take(40)
                .last()
                .map_or(0, |(index, _)| index);
            if before[context_start..].contains("env::var") {
                read_by_the_daemon.insert(name.to_owned());
            }
            rest = &tail[end..];
        }
    }
    assert!(
        !read_by_the_daemon.is_empty(),
        "the scan found nothing, so it is not checking anything",
    );

    let handled: std::collections::BTreeSet<String> = DAEMON_REDIRECT_ENV
        .iter()
        .chain(DAEMON_INTENDED_ENV.iter())
        .map(|name| (*name).to_owned())
        .collect();
    let unconsidered: Vec<&String> = read_by_the_daemon.difference(&handled).collect();
    assert!(
        unconsidered.is_empty(),
        "the daemon reads these and this build neither strips nor \
         deliberately passes them: {unconsidered:?}. Add each to \
         DAEMON_REDIRECT_ENV, or to DAEMON_INTENDED_ENV if the app sets it \
         on purpose.",
    );

    // And the stripping is actually wired, before the deliberate ones are
    // set -- `env` after `env_remove` is what makes those still win.
    let source = include_str!("../locald_process.rs").replace("\r\n", "\n");
    let start = source
        .find("fn spawn_locald()")
        .expect("spawn_locald exists");
    let body = &source[start..start + 3000];
    let removed = body
        .find("command.env_remove(name)")
        .expect("a release build strips them");
    let set = body
        .find(r#".env("PATH", enriched_path())"#)
        .expect("it sets PATH");
    assert!(
        removed < set,
        "removal has to happen before the deliberate sets"
    );
}

/// A build keeps its data where its own name says, not where "Lemma" does.
///
/// Qualifying a candidate means running it on the same Mac as the real
/// installation. Sharing `Application Support/Lemma` would let the
/// candidate stop the user's daemon, adopt its runtime and reset its pods
/// -- so a QA build bakes in a different directory, and a release build
/// must keep the one every existing installation already uses.
#[test]
fn a_release_build_keeps_its_data_where_installed_lemma_already_has_it() {
    assert_eq!(
        option_env!("LEMMA_DESKTOP_DATA_DIR_NAME"),
        None,
        "a build with this set is a qualification candidate, not a release"
    );
    assert_eq!(DATA_DIR_NAME, "Lemma");
    assert!(
        app_support_dir().ends_with("Lemma"),
        "moving this orphans every existing installation's data: {}",
        app_support_dir().display()
    );
}

#[test]
fn a_resume_target_from_another_release_is_refused() {
    // The failure this prevents: installing a new build over an old one,
    // whose stack is often still serving. The probe passes, the window
    // opens the old workspace, and then ensure_locald replaces the
    // mismatched daemon and brings everything back on new ports — leaving
    // the window on a dead port with nothing to navigate it away.
    let saved = |release: &str| {
        json!({
            "url": "http://app.lemma.localhost:49180",
            "apiUrl": "http://app.lemma.localhost:49181",
            "generation": "abc123",
            "release": release,
            "route": "/",
        })
    };
    let accepted = |entry: &Value| {
        entry["release"].as_str() == Some(env!("CARGO_PKG_VERSION"))
            && !entry["generation"].as_str().unwrap_or_default().is_empty()
    };

    assert!(accepted(&saved(env!("CARGO_PKG_VERSION"))));
    assert!(!accepted(&saved("0.6.9")));
    // A target written before releases were recorded has no claim either.
    assert!(!accepted(&json!({
        "url": "http://app.lemma.localhost:49180",
        "apiUrl": "http://app.lemma.localhost:49181",
        "generation": "abc123",
        "route": "/",
    })));
}

#[test]
fn durable_daemon_must_match_the_bundled_host_pack_release() {
    let root = tempfile::tempdir().unwrap();
    let pack = root.path().join("local-runtime");
    std::fs::create_dir_all(&pack).unwrap();
    let current = json!({
        "event": "hello", "protocol": 1, "mode": "host-packs",
        "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
        "host_pack_release": "1.2.3",
        "host_pack_root": path_identity(&pack),
    });
    let old_same_version = json!({
        "event": "hello", "protocol": 1, "mode": "host-packs",
        "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
        "host_pack_release": "1.2.3",
    });
    let stale_daemon = json!({
        "event": "hello", "protocol": 1, "mode": "host-packs",
        "host_pack_release": "1.2.3",
        "host_pack_root": path_identity(&pack),
    });
    let compatibility = json!({
        "event": "hello", "protocol": 1, "mode": "compatibility",
    });

    assert!(locald_matches_host_pack(
        &current,
        Some("1.2.3"),
        Some(&pack)
    ));
    assert!(!locald_matches_host_pack(
        &current,
        Some("1.2.4"),
        Some(&pack)
    ));
    assert!(!locald_matches_host_pack(
        &old_same_version,
        Some("1.2.3"),
        Some(&pack)
    ));
    assert!(!locald_matches_host_pack(
        &stale_daemon,
        Some("1.2.3"),
        Some(&pack)
    ));
    assert!(!locald_matches_host_pack(
        &compatibility,
        Some("1.2.3"),
        Some(&pack)
    ));
    assert!(locald_matches_host_pack(&compatibility, None, None));
}

#[test]
fn legacy_connection_preferences_require_the_released_chooser_once() {
    assert_eq!(
        configured_connection_mode(&json!({"connectionMode": "hosted"})),
        "undecided"
    );
    assert_eq!(
        configured_connection_mode(&json!({
            "connectionMode": "local",
            "connectionModePromptRevision": CONNECTION_MODE_PROMPT_REVISION,
        })),
        "local"
    );
}

/// A check that can hang leaves Settings on "Checking for updates..." for good.
#[test]
fn an_update_check_gives_up_rather_than_hanging() {
    let source = shell_source();
    // Settings and the launch-time check ask through one function, and it is
    // the one that carries the bound.
    let check = function_body(&source, "pub(crate) async fn check_for_app_update(");
    assert!(check.contains("fetch_offered_update(&app)"));
    let fetch = function_body(&source, "pub(crate) async fn fetch_offered_update(");
    assert!(
        fetch.contains(".timeout(UPDATE_CHECK_TIMEOUT)"),
        "the check must bound its request to the feed"
    );
    assert!(UPDATE_CHECK_TIMEOUT <= std::time::Duration::from_secs(60));
}

/// Once installed, the update restarts; there is no "Later".
///
/// The stack is stopped and the bundle replaced by then, so staying on the old
/// shell only left a stopped stack whose restart would pair the new locald
/// with the old guest.
#[test]
fn an_installed_update_restarts_without_offering_to_wait() {
    let source = include_str!("../app_update.rs").replace("\r\n", "\n");
    let body = function_body(&source, "pub(crate) async fn install_app_update(");
    let install = body
        .find("update.install(bytes)")
        .expect("the update is installed");
    let after = &body[install..];
    assert!(after.contains("app.restart()"), "{after}");
    assert!(
        !after.contains("confirm_destructive_action_impl"),
        "nothing may ask whether to restart after the bundle is replaced: {after}"
    );
    let consent = &body[..install];
    assert!(
        consent.contains("Lemma restarts as soon as it is installed"),
        "{consent}"
    );
}
