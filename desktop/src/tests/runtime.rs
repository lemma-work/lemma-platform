use super::*;

#[test]
fn no_lock_is_held_across_the_runtime_install() {
    // `ensure_locald` used to take `locald_connect` and then install the
    // runtime -- hundreds of megabytes -- while holding it. Local settings'
    // heartbeat takes the same path, so opening settings during a first
    // install blocked for the whole install. The install has its own
    // single-flight now, and it must come first.
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn ensure_locald(app: &AppHandle)");
    let install = body
        .find("ensure_runtime_artifacts(app)")
        .expect("ensure_locald installs the runtime");
    let connect = body
        .find("locald_connect.lock()")
        .expect("ensure_locald takes the connect guard");
    assert!(
        install < connect,
        "the runtime install must happen before the connect guard is taken, \
         not inside it"
    );
    assert!(
        body.contains("runtime_install.lock()"),
        "the install still needs its own single-flight so two callers cannot \
         download at once"
    );
}

/// On Windows the updater exits this process to run the installer, so
/// there is no line after `install` in which to record anything. An
/// installer cancelled at the UAC prompt, or interrupted by a reboot, left
/// the app running the old version with nothing anywhere saying an update
/// had been attempted -- so the next launch, and the user, had no account
/// of why they were still on the version they were on.
#[test]
fn an_update_attempt_is_read_against_the_version_actually_running() {
    let attempt = json!({"schema_version": 1, "from": "0.7.2", "to": "0.7.3"});

    assert_eq!(
        classify_update_attempt(Some(&attempt), "0.7.3"),
        UpdateAttempt::Landed { to: "0.7.3".into() },
        "running the version it aimed at is the update having happened"
    );
    assert_eq!(
        classify_update_attempt(Some(&attempt), "0.7.2"),
        UpdateAttempt::DidNotLand { to: "0.7.3".into() },
        "still on the version it started from means the installer never ran"
    );
    // Neither end matches: a third version got installed in between, and
    // this record explains nothing about the launch reading it.
    assert_eq!(
        classify_update_attempt(Some(&attempt), "0.8.0"),
        UpdateAttempt::Unexplained
    );
    assert_eq!(
        classify_update_attempt(Some(&json!({"schema_version": 1})), "0.7.2"),
        UpdateAttempt::Unexplained,
        "a record missing its versions cannot be acted on"
    );
    assert_eq!(classify_update_attempt(None, "0.7.2"), UpdateAttempt::None);
}

/// The record lives where an install cannot take it.
///
/// Measured on Windows rather than assumed: the NSIS installer puts Lemma
/// in `%LOCALAPPDATA%\Lemma`, which is also where `locald` keeps its
/// state, so "beside the app" and "in the state root" are the same
/// directory there. What separates them is that the uninstaller removes
/// only the files it installed -- verified by leaving a file in
/// `locald\` across an `uninstall.exe /S` and finding it still there --
/// so anything under `locald` survives an install, an update and an
/// uninstall alike, and the shipped executables do not.
///
/// Which makes the state root the only correct home for a record whose
/// whole job is to outlive the installer.
#[test]
fn the_update_record_is_kept_where_an_install_cannot_take_it() {
    let path = update_attempt_path();
    assert!(
        path.starts_with(locald_root()),
        "the record has to live in the state root, not beside the app: {}",
        path.display()
    );
    assert_eq!(
        path.file_name().and_then(std::ffi::OsStr::to_str),
        Some("shell-update.json"),
    );
}

#[test]
fn nothing_in_setup_installs_the_runtime_on_the_main_thread() {
    // `setup` runs before the event loop begins pumping, so anything slow
    // there freezes a window that is already on screen. `ensure_locald`
    // unpacks the runtime on a first run or an upgrade -- hundreds of
    // megabytes -- and then waits for the daemon, which is how the splash
    // came to sit on "Starting Lemma." with no progress for minutes.
    //
    // Both launch paths, resume and cold start, must hand that to a worker.
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let setup = {
        let start = source.find(".setup(move |app| {").expect("setup exists");
        let end = source[start..]
            .find("\n        .build(")
            .or_else(|| source[start..].find("\n        .run("))
            .map_or(source.len(), |offset| start + offset);
        &source[start..end]
    };
    for (index, _) in setup.match_indices("ensure_locald(&handle)") {
        let preceding = &setup[..index];
        let spawned = preceding.rfind("std::thread::spawn");
        let closed = preceding.rfind("});");
        assert!(
            spawned.is_some() && spawned > closed,
            "an ensure_locald in setup is not inside a spawned thread"
        );
    }
}

/// The feed's compatibility block decides before anything is downloaded.
#[test]
fn an_update_that_would_strand_local_data_says_so_first() {
    let same = LemmaUpdateMetadata {
        postgres_major: Some(18),
        runtime_download_bytes: Some(531_000_000),
    };
    assert_eq!(same.compatibility_with(Some(18)), "compatible");
    assert_eq!(
        same.compatibility_with(Some(16)),
        "migration-unavailable",
        "a Postgres major bump cannot open the existing cluster"
    );

    // The caller blocks an unknown pairing if an installed runtime has data.
    assert_eq!(same.compatibility_with(None), "unknown");
    assert_eq!(
        LemmaUpdateMetadata::default().compatibility_with(Some(18)),
        "unknown"
    );
}

#[test]
fn update_preflight_rejects_data_reset_and_unsupported_migrations() {
    for windows in [false, true] {
        for has_runtime in [false, true] {
            assert!(
                ensure_update_preserves_data(true, has_runtime, "compatible", windows).is_err()
            );
        }
    }
    for compatibility in ["unknown", "requires-reset", "migration-unavailable"] {
        assert!(ensure_update_preserves_data(false, true, compatibility, false).is_err());
    }
    assert!(ensure_update_preserves_data(false, true, "compatible", true).is_err());
    assert!(ensure_update_preserves_data(false, true, "compatible", false).is_ok());
    assert!(ensure_update_preserves_data(false, false, "unknown", true).is_ok());
}

/// A feed without the block, or with junk in it, is read safely.
#[test]
fn update_metadata_tolerates_a_feed_that_does_not_carry_it() {
    let absent = lemma_update_metadata(&json!({"version": "0.8.0"}));
    assert_eq!(absent.postgres_major, None);
    assert_eq!(absent.runtime_download_bytes, None);

    let wrong_types = lemma_update_metadata(&json!({
        "lemma": {"postgres_major": "eighteen", "runtime_download_bytes": []}
    }));
    assert_eq!(wrong_types.postgres_major, None);
    assert_eq!(wrong_types.runtime_download_bytes, None);

    let good = lemma_update_metadata(&json!({
        "lemma": {"postgres_major": 18, "runtime_download_bytes": 531_000_000_u64}
    }));
    assert_eq!(good.postgres_major, Some(18));
    assert_eq!(good.runtime_download_bytes, Some(531_000_000));
}

#[test]
fn update_metadata_uses_the_selected_platform_and_never_another_platforms_fallback() {
    let feed = json!({"lemma": {
        "postgres_major": 16, "runtime_download_bytes": 10,
        "platforms": {
            "darwin-aarch64": {"postgres_major": 18, "runtime_download_bytes": 20},
            "windows-x86_64": {"postgres_major": 18, "runtime_download_bytes": 30}
        }
    }});
    assert_eq!(
        lemma_update_metadata_for(&feed, "darwin-aarch64").runtime_download_bytes,
        Some(20)
    );
    let windows = lemma_update_metadata_for(&feed, "windows-x86_64");
    assert_eq!(windows.runtime_download_bytes, Some(30));
    assert_eq!(windows.postgres_major, Some(18));
    assert_eq!(
        lemma_update_metadata_for(&feed, "missing-target").postgres_major,
        None
    );
}

/// The updater is never reachable from a remote origin.
///
/// `workspace.json` grants commands to the locald-served app URL and to
/// lemma.work. A permission that can replace the application binary,
/// reachable from a page served over the network, would be a
/// remote-code-execution primitive -- so the grant lives only on the
/// control window, and nothing may hand the plugin's own permissions to
/// anyone.
#[test]
fn no_capability_exposes_the_updater_to_a_remote_origin() {
    for (name, source) in [
        ("main", include_str!("../../capabilities/main.json")),
        ("control", include_str!("../../capabilities/control.json")),
        (
            "workspace",
            include_str!("../../capabilities/workspace.json"),
        ),
    ] {
        assert!(
            !source.contains("\"updater:"),
            "{name} must not grant the updater plugin's own permissions",
        );
        assert!(
            !source.contains("\"process:"),
            "{name} must not grant process control",
        );
    }
    let workspace = include_str!("../../capabilities/workspace.json").replace("\r\n", "\n");
    for command in ["allow-check-for-app-update", "allow-install-app-update"] {
        assert!(
            !workspace.contains(command),
            "a remote origin must not be able to replace the application",
        );
    }
    assert!(include_str!("../../capabilities/control.json").contains("allow-install-app-update"));
}

#[test]
fn the_updater_config_keeps_its_transport_and_build_boundaries() {
    let config = include_str!("../../tauri.conf.json").replace("\r\n", "\n");
    assert!(config.contains("\"updater\""));
    assert!(
        !config.contains("dangerousInsecureTransportProtocol"),
        "an update served over plain HTTP is an update anyone can forge",
    );
    assert!(
        config.contains("https://github.com/"),
        "every endpoint must be HTTPS",
    );
    // `tauri build` runs from five places with no signing key -- CI's build
    // check, the Windows check, two nightly jobs and `make desktop-dmg` --
    // and every one of them fails if the base config demands updater
    // artifacts. The flag belongs only in the overlay the release merges.
    assert!(
        !config.contains("createUpdaterArtifacts"),
        "the artifact flag belongs in tauri.updater.conf.json, not the base config",
    );
    assert!(include_str!("../../tauri.updater.conf.json").contains("createUpdaterArtifacts"));

    // The CSP is untouched: the check runs in Rust precisely so the webview
    // that also hosts the remote workspace never gains github.com.
    assert!(
        config.contains("connect-src 'self' ipc: http://ipc.localhost"),
        "checking from Rust is what keeps the webview's network policy narrow",
    );
}

/// An update is only offered by a build that could actually install one.
///
/// `tauri.conf.json` ships `"pubkey": ""` until the signing keypair exists,
/// and an empty key is not a permissive setting: `verify_signature` decodes
/// it and errors, so every install fails -- at the last step, after the app
/// has stopped the user's daemon to make room. The config test above passed
/// throughout, because it never looked at the key.
///
/// Two independent guards, because they fail at opposite ends: this one
/// stops the *app* offering what it cannot verify, and the release workflow
/// refuses to build at all with the key empty.
#[test]
fn a_build_with_no_verification_key_does_not_offer_updates() {
    let configured = updater_key_configured();
    let committed = serde_json::from_str::<Value>(include_str!("../../tauri.conf.json"))
        .expect("the config parses")
        .pointer("/plugins/updater/pubkey")
        .and_then(Value::as_str)
        .map(|key| !key.trim().is_empty())
        .expect("the config declares an updater pubkey field");
    assert_eq!(
        configured, committed,
        "the runtime gate must read the key the build actually ships",
    );
    if !configured {
        assert!(
            !updates_enabled(),
            "a build that cannot verify an update must not offer one",
        );
    }
}

/// Install failures say what happened and what to try.
///
/// This mapper had one branch, and it told the reader to "publish its
/// runtime artifacts" -- an instruction to a maintainer, shipped to
/// strangers. Everything else reached the error screen verbatim.
#[test]
fn install_failures_are_explained_rather_than_dumped() {
    let cases = [
        ("artifact download failed with HTTP 404", "superseded"),
        (
            "artifact download failed with HTTP 403",
            "proxy or firewall",
        ),
        ("artifact download failed with HTTP 429", "rate-limited"),
        (
            "could not connect to the artifact host",
            "internet connection",
        ),
        ("the request timed out", "resumes from where it stopped"),
        (
            "artifact size or SHA-256 did not match the signed manifest",
            "captive Wi-Fi portal",
        ),
        (
            "signed runtime release 0.9.0 does not match desktop release 0.7.0",
            "do not match",
        ),
    ];
    for (raw, expected) in cases {
        let shown = actionable_runtime_install_error(raw);
        assert!(
            shown.contains(expected),
            "{raw:?} should explain {expected:?}, got {shown:?}"
        );
        assert!(
            !shown.contains("HTTP 4"),
            "a status code is not an explanation: {shown:?}"
        );
    }

    // Nobody outside this team can act on either of these.
    let shown = actionable_runtime_install_error("artifact download failed with HTTP 404");
    assert!(!shown.contains("Publish"), "{shown:?}");
    assert!(!shown.contains("PR test DMG"), "{shown:?}");

    // A message that is already specific keeps its numbers.
    let disk = "not enough disk space for Lemma's local runtime: 7 GiB required, 3 GiB available";
    assert_eq!(actionable_runtime_install_error(disk), disk);
}

/// Each platform's data disk, spelled the way that platform spells it.
///
/// The Windows branch pointed at `runtime/windows`, which nothing creates.
/// So on Windows `has_local_runtime_data` answered "no data" for a real
/// installation — the guard that refuses to reset a user's data during an
/// update was leaning entirely on the config check — and Recovery reported
/// the disk as zero bytes.
#[test]
fn the_managed_data_disk_is_the_one_this_platform_actually_writes() {
    let disk = managed_data_disk();
    let tail: Vec<_> = disk
        .components()
        .rev()
        .take(2)
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect();

    if cfg!(windows) {
        assert_eq!(tail, vec!["ext4.vhdx", "wsl"], "{}", disk.display());
    } else {
        assert_eq!(tail, vec!["data.raw", "macos"], "{}", disk.display());
    }
    assert!(
        disk.starts_with(locald_root()),
        "the disk belongs to this installation: {}",
        disk.display()
    );
}

/// An update that fails after the stack is stopped must say the previous
/// version survived, and whether it is running.
///
/// `install_app_update` stops locald before installing, on purpose: an
/// in-place update writes to the same path, so a stale daemon would be
/// adopted by the new shell. But when the install then failed it returned
/// the error and stopped there, leaving someone in Local settings looking
/// at a workspace whose backend had gone, with nothing on screen offering
/// to bring it back.
#[test]
fn a_failed_update_says_the_previous_version_survived_it() {
    let recovered = failed_install_message("the bundle is not writable", None);
    assert!(
        recovered.contains("previous version is still installed"),
        "{recovered}"
    );
    assert!(
        recovered.contains("starting again"),
        "the user needs to know the stack is coming back: {recovered}"
    );

    let stranded = failed_install_message(
        "the bundle is not writable",
        Some("daemon did not answer".into()),
    );
    assert!(
        stranded.contains("previous version is still installed"),
        "{stranded}"
    );
    assert!(
        stranded.contains("Recovery"),
        "a stack that stayed down has to name the way out: {stranded}"
    );
    assert!(
        stranded.contains("daemon did not answer"),
        "the reason it stayed down is the actionable half: {stranded}"
    );
}

#[test]
fn unpublished_online_runtime_error_is_actionable_and_logged_in_app() {
    // "Actionable" now means actionable *by the person reading it*. This
    // used to assert the previous copy -- "publish its runtime artifacts,
    // or use the compressed PR test DMG for this exact commit" -- which is
    // an instruction to a maintainer that shipped to strangers.
    let message = actionable_runtime_install_error(
        "could not install local runtime: artifact download failed with HTTP 404",
    );
    assert!(message.contains("superseded"), "{message}");
    assert!(message.contains("download the current one"), "{message}");
    assert!(!message.contains("Publish"), "{message}");
    assert!(!message.contains("PR test DMG"), "{message}");

    let splash = include_str!("../../ui/index.html").replace("\r\n", "\n");
    assert!(splash.contains("diagnosticLogs: (source, cursor = null)"));
    assert!(splash.contains("refreshDiagnosticLog"));
    assert!(splash.contains("id=\"log-tabs\""));
    assert!(splash.contains("View log"));
}

#[test]
fn local_settings_exposes_honest_runtime_repair_and_rollback_boundaries() {
    let html = include_str!("../../ui/control.html").replace("\r\n", "\n");
    let script = include_str!("../../ui/control.js").replace("\r\n", "\n");

    assert!(html.contains("Signed release lifecycle"));
    assert!(script.contains("repair_runtime"));
    assert!(script.contains("open_developer_tools"));
    assert!(html.contains("Developer tools"));
    assert!(html.contains("id=\"network-contract\""));
    assert!(html.contains("id=\"connector-callback\""));
    assert!(script.contains("snapshot.state?.api_url"));
    assert!(!html.contains("http://app.lemma.localhost:8711/api/v1/connectors"));
    // The rollback notice used to be here, toggled `hidden = rollbackAvailable`
    // against a value hardcoded `false` -- so it was *permanently* on
    // screen, explaining a feature that does not exist. A standing
    // paragraph about something that has never happened is a bug, not a
    // flag, and the Previous runtime card now says what is actually true.
    assert!(
        !html.contains("Rollback stays unavailable"),
        "a notice that can never be dismissed is not a boundary, it is noise",
    );
    assert!(html.contains("Reinstalling an earlier Lemma from the release page"));
    assert!(html.contains("Databases, files, and workspaces are preserved"));
}

#[test]
fn packaged_runtime_root_never_falls_back_to_the_build_checkout() {
    let executable = std::path::Path::new("/Applications/Lemma.app/Contents/MacOS/lemma-desktop");
    let checkout = std::path::Path::new("/Users/developer/lemma-platform/desktop");

    assert_eq!(
        default_runtime_root(Some(executable), checkout, false),
        std::path::Path::new("/Applications/Lemma.app/Contents/MacOS")
    );
    assert_eq!(
        default_runtime_root(Some(executable), checkout, true),
        std::path::Path::new("/Users/developer/lemma-platform")
    );
}
