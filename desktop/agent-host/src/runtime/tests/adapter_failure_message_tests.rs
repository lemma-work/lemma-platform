use super::authentication_hint;

/// An agent that just dies went wrong on this computer too.
///
/// It does not report itself: there is no JSON-RPC error to forward, only
/// an exit status, which since this host took ownership of the child
/// process arrives as "Process exited with ...". That matched none of the
/// adapter-internal shapes, so a crash mid-answer was framed as a bare
/// failure with nothing in it to act on -- while the person watching had
/// just seen half an answer appear and stop.
#[test]
fn an_agent_that_exits_non_zero_is_framed_like_any_other_adapter_failure() {
    let framed = super::adapter_failure_message("Codex", "Process exited with exit status: 23")
        .expect("a crashed adapter has to say whose crash it was");
    assert!(
        framed.starts_with("Codex encountered an error on this computer"),
        "{framed}"
    );
    assert!(framed.contains("exit status: 23"), "{framed}");

    // With its own last words, when it managed any.
    let with_stderr = super::adapter_failure_message(
        "Codex",
        "Process exited with exit status: 1: panicked at src/main.rs",
    )
    .expect("framed");
    assert!(
        with_stderr.contains("panicked at src/main.rs"),
        "{with_stderr}"
    );

    // Still not everything: an ordinary message is left alone.
    assert!(super::adapter_failure_message("Codex", "run deadline elapsed").is_none());
}

#[test]
fn a_signed_out_agent_is_told_to_sign_in_rather_than_reported_as_internal() {
    // Verbatim from Claude Code. Accurate and useless: it names no agent,
    // suggests nothing to do, and reads like a defect in Lemma rather than
    // a session that needs renewing.
    let raw = concat!(
        "Internal error: Failed to authenticate: OAuth session expired and ",
        r#"could not be refreshed: {"errorKind": "authentication_failed"}"#
    );
    let hint = authentication_hint("Claude Code", raw).expect("recognised as an auth failure");

    assert!(
        hint.contains("Claude Code"),
        "name the agent that needs signing in"
    );
    assert!(hint.contains("sign in") || hint.contains("signed in"));
    assert!(!hint.contains("errorKind"), "no adapter internals");
}

#[test]
fn other_phrasings_of_the_same_failure_are_recognised() {
    for raw in [
        "authentication_failed",
        "Error: not logged in",
        "request failed: 401 unauthorized, session invalid",
    ] {
        assert!(
            authentication_hint("OpenCode", raw).is_some(),
            "{raw:?} is an authentication failure",
        );
    }
}

#[test]
fn a_conversation_keeps_one_working_directory_across_turns() {
    // ACP's session/load takes a working directory. Keying this on the run
    // meant every follow-up turn asked the agent to resume a session whose
    // cwd had just been deleted — so resumption could never succeed, and
    // for OpenCode the failed load left the connection unable to open a new
    // session either. First message answered, second did not.
    let paths = super::HostPaths::under("/tmp/example");
    let target = uuid::Uuid::from_u128(1);
    let conversation = uuid::Uuid::from_u128(2);

    let first = super::scratch_directory(&paths, target, conversation);
    let second = super::scratch_directory(&paths, target, conversation);
    assert_eq!(
        first, second,
        "both turns share the conversation's directory"
    );

    let other = super::scratch_directory(&paths, target, uuid::Uuid::from_u128(3));
    assert_ne!(first, other, "different conversations stay isolated");
}

#[test]
fn old_conversation_files_survive_reopening_and_other_conversations() {
    let directory = tempfile::tempdir().unwrap();
    let paths = super::HostPaths::under(directory.path().join("Δ workspace"));
    let target = uuid::Uuid::new_v4();
    let conversation = uuid::Uuid::new_v4();
    let first = super::prepare_conversation_directory(&paths, target, conversation).unwrap();
    std::fs::write(first.join("user-work.txt"), "keep this work").unwrap();
    #[cfg(unix)]
    {
        let old = std::time::SystemTime::now() - std::time::Duration::from_secs(30 * 86400);
        std::fs::File::open(&first)
            .unwrap()
            .set_modified(old)
            .unwrap();
    }
    super::prepare_conversation_directory(&paths, target, uuid::Uuid::new_v4()).unwrap();
    let reopened = super::prepare_conversation_directory(&paths, target, conversation).unwrap();
    assert_eq!(first, reopened);
    assert_eq!(
        std::fs::read_to_string(reopened.join("user-work.txt")).unwrap(),
        "keep this work"
    );
    let spec: super::RunSpec = serde_json::from_value(serde_json::json!({
        "agent_run_id": uuid::Uuid::new_v4(), "conversation_id": conversation,
        "harness_id": uuid::Uuid::new_v4(), "profile_revision": "test",
        "system_prompt": "test", "prompt": [],
        "workspace_cwd": "/home/user/lemma/c/2026-09-07/new-layout",
        "resume_session_id": "existing-provider-session",
        "run_deadline": chrono::Utc::now(),
    }))
    .unwrap();
    assert_eq!(
        super::prepare_run_directory(&paths, target, &spec).unwrap(),
        first,
        "an upgrade must not relocate a provider session's existing cwd"
    );
}

/// A folder the person picked on this computer is where the agent works.
///
/// It wins over the legacy scratch directory as well, so binding a folder takes
/// effect on a conversation that has already run somewhere else -- otherwise the
/// choice would appear to do nothing on the conversation you were in when you
/// made it.
#[test]
fn a_folder_bound_on_this_computer_is_the_working_directory() {
    let temporary = tempfile::tempdir().unwrap();
    let paths = super::HostPaths::under(temporary.path().join("host"));
    std::fs::create_dir_all(&paths.root).unwrap();
    let target = uuid::Uuid::new_v4();
    let conversation = uuid::Uuid::now_v7();

    // The conversation has already worked in the ordinary place.
    let legacy = super::prepare_conversation_directory(&paths, target, conversation).unwrap();

    let project = temporary.path().join("my project");
    std::fs::create_dir(&project).unwrap();
    std::fs::write(
        &paths.folders,
        serde_json::to_vec(&serde_json::json!({
            conversation.to_string(): project.to_str().unwrap(),
        }))
        .unwrap(),
    )
    .unwrap();

    let spec: super::RunSpec = serde_json::from_value(serde_json::json!({
        "agent_run_id": uuid::Uuid::new_v4(), "conversation_id": conversation,
        "harness_id": uuid::Uuid::new_v4(), "profile_revision": "test",
        "system_prompt": "test", "prompt": [],
        "workspace_cwd": "/home/user/lemma/c/2026-09-10/somewhere-else",
        "run_deadline": chrono::Utc::now(),
    }))
    .unwrap();

    assert_eq!(
        super::prepare_run_directory(&paths, target, &spec).unwrap(),
        project,
        "the bound folder wins over both the run's cwd and the legacy directory"
    );
    assert_ne!(legacy, project);

    // A binding whose folder has gone falls back rather than failing the run.
    std::fs::remove_dir(&project).unwrap();
    assert_eq!(
        super::prepare_run_directory(&paths, target, &spec).unwrap(),
        legacy
    );
}

#[test]
fn native_directory_instruction_encodes_paths_without_inventing_a_mount() {
    for cwd in [
        "/Users/test/Δ project",
        "C:\\Users\\test\\My Project",
        "/tmp/quote\"\nfolder",
    ] {
        let prompt = super::host_directory_instructions(cwd);
        assert!(prompt.contains(&serde_json::to_string(cwd).unwrap()));
        assert!(prompt.contains("not mounted on this computer"));
        assert!(prompt.contains("not an access grant"));
        // The mount this must not invent is a literal one. Naming a container
        // root here told an agent whose native tools run on the host that
        // the container root was somewhere it could `cd` -- and it tried, and the
        // command failed. The sandbox path belongs to the run that was given
        // one, not to a sentence written months earlier.
        assert!(
            !prompt.contains("/home/user"),
            "the native instruction must not name a container root: {prompt}"
        );
    }
}

#[test]
fn an_adapter_internal_error_says_whose_it_is() {
    // Verbatim from OpenCode. Names no agent, points nowhere, and reads
    // like a defect in Lemma rather than a session that would not start.
    let raw = r#"Internal error: OpenCode service failure: {"service": "session"}"#;
    let framed = super::adapter_failure_message("OpenCode", raw).expect("framed");

    assert!(framed.starts_with("OpenCode encountered an error"));
    // The adapter's own words survive: they are the only thing that
    // explains an unfamiliar failure.
    assert!(framed.contains(raw));
}

#[test]
fn an_ordinary_failure_is_not_dressed_up_as_an_adapter_fault() {
    for raw in [
        "Agent Host run deadline elapsed; the provider process was terminated",
        "adapter executable opencode was not found",
    ] {
        assert!(
            super::adapter_failure_message("OpenCode", raw).is_none(),
            "{raw:?}"
        );
    }
}

#[test]
fn an_unfamiliar_failure_is_left_exactly_as_it_came() {
    // Guessing would bury the one line that explains an unknown failure.
    for raw in [
        "adapter executable opencode was not found",
        "provider process exited with status 1",
        "ACP probe timed out",
    ] {
        assert!(authentication_hint("OpenCode", raw).is_none(), "{raw:?}");
    }
}

/// A coding agent out of quota is not broken, and a terminal will not help.
///
/// Verbatim shape from Claude Code: an internal error whose structured data
/// names the kind. The kind decides it, not the wording.
#[test]
fn an_agent_at_its_usage_limit_is_told_when_it_resets() {
    let raw = concat!(
        "Internal error: You've hit your session limit · resets 2pm (Asia/Calcutta): ",
        r#"{"errorKind": "rate_limit"}"#
    );

    let message = super::usage_limit_message("Claude Code", raw).expect("a usage limit");

    assert!(
        message.starts_with("Claude Code has reached its usage limit on this computer"),
        "{message}"
    );
    assert!(message.contains("resets 2pm (Asia/Calcutta)"), "{message}");
    assert!(message.contains("another runtime"), "{message}");
    assert!(!message.contains("terminal"), "{message}");
    assert!(!message.contains("errorKind"), "no adapter internals");
    assert!(
        super::authentication_hint("Claude Code", raw).is_none(),
        "not a sign-in problem"
    );
}

#[test]
fn a_usage_limit_is_recognised_by_its_kind_whatever_it_says() {
    // Pretty-printed data, as an ACP error formats it, and no reset time.
    let raw = "Internal error: quota gone: {\n  \"errorKind\": \"usage_limit\"\n}";
    let message = super::usage_limit_message("OpenCode", raw).expect("a usage limit");
    assert!(message.contains("Try again once it resets"), "{message}");

    // The same words with another kind, or none, are not a usage limit.
    assert!(
        super::usage_limit_message(
            "Codex",
            r#"Internal error: resets later: {"errorKind": "authentication_failed"}"#
        )
        .is_none()
    );
    assert!(super::usage_limit_message("Codex", "You've hit your session limit").is_none());
}

/// A `resets` inside the structured data is not the adapter telling us when
/// the limit lifts, and its JSON must not leak into the guidance.
#[test]
fn a_reset_time_is_read_from_the_message_not_its_data() {
    let raw =
        r#"Internal error: quota gone: {"errorKind": "rate_limit", "detail": "resets tomorrow"}"#;
    let message = super::usage_limit_message("Claude Code", raw).expect("a usage limit");
    assert!(message.contains("Try again once it resets"), "{message}");
    assert!(!message.contains("tomorrow"), "{message}");
    assert!(!message.contains('"'), "{message}");
}

#[test]
fn the_structured_data_is_cut_from_the_message() {
    assert_eq!(
        super::without_error_data(r#"Internal error: limit hit: {"errorKind": "rate_limit"}"#),
        "Internal error: limit hit"
    );
    assert_eq!(
        super::without_error_data("plain {not json"),
        "plain {not json"
    );
}

/// A start that ran out of time says so, rather than calling a working agent
/// broken and sending the person to a terminal to debug it.
#[test]
fn a_start_that_timed_out_is_reported_as_slow_rather_than_broken() {
    for raw in [
        r#"Internal error: "the agent did not answer session/new within 30s""#,
        r#"Internal error: {"details": "workspace routing discovery timed out"}"#,
    ] {
        let framed = super::start_timeout_message("Codex", raw).expect("a start timeout");
        assert!(
            framed.starts_with("Codex did not start in time on this computer"),
            "{framed}"
        );
        assert!(framed.contains("send the message again"), "{framed}");
        assert!(!framed.contains("encountered an error"), "{framed}");
    }
}

/// Anything that is not a timeout keeps the wording that fits it.
#[test]
fn only_a_timeout_is_reported_as_a_slow_start() {
    for raw in [
        r#"Internal error: OpenCode service failure: {"service": "session"}"#,
        "Process exited with exit status: 1",
        "Failed to authenticate: OAuth session expired",
        r#"Internal error: {"details": "workspace routing discovery unauthorized (401)"}"#,
        r#"Invalid params: "unknown model provider \"nope\" in config.toml""#,
    ] {
        assert!(
            super::start_timeout_message("Codex", raw).is_none(),
            "{raw:?}"
        );
    }
}
