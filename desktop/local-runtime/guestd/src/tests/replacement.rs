//! Replacing a running sandbox: a newer generation, a swap a previous guestd
//! died in the middle of, and the credential handed to a new container.

use super::run_contract::workspace_parameters;
use super::*;

fn swap_service(outputs: Vec<Output>) -> (tempfile::TempDir, GuestService<FakeEngine>) {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(outputs),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    (root, service)
}

fn strings(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|part| (*part).to_owned()).collect()
}

/// A replacement whose `run` fails puts the running sandbox back as it was.
///
/// The old container is renamed aside rather than removed, so a failed start
/// -- an image that will not run, a port the engine refuses -- leaves the user
/// with the sandbox they had instead of none.
#[test]
fn a_failed_replacement_restores_the_running_sandbox() {
    let (_root, service) = swap_service(vec![
        output(false, ""), // no leftover aside
        output(true, ""),  // rename aside
        output(false, ""), // run fails
        output(true, ""),  // clear whatever run left
        output(true, ""),  // rename back
    ]);
    let run = strings(&["run", "--name", "lemma-box-1", "image"]);

    let error = service
        .replace_and_run("lemma-box-1", &run, true)
        .expect_err("the run failure is reported");
    assert_eq!(error.code, "guest_engine_failed");
    assert_eq!(
        service.engine.commands.lock().unwrap().as_slice(),
        [
            strings(&["rm", "--force", "lemma-box-1-replaced"]),
            strings(&["rename", "lemma-box-1", "lemma-box-1-replaced"]),
            run.clone(),
            strings(&["rm", "--force", "lemma-box-1"]),
            strings(&["rename", "lemma-box-1-replaced", "lemma-box-1"]),
        ]
    );
}

#[test]
fn a_successful_replacement_removes_the_old_container_only_afterwards() {
    let (_root, service) = swap_service(vec![
        output(false, ""),
        output(true, ""),
        output(true, "new-id"),
        output(true, ""),
    ]);
    let run = strings(&["run", "--name", "lemma-box-1", "image"]);

    service.replace_and_run("lemma-box-1", &run, true).unwrap();
    assert_eq!(
        service.engine.commands.lock().unwrap().as_slice(),
        [
            strings(&["rm", "--force", "lemma-box-1-replaced"]),
            strings(&["rename", "lemma-box-1", "lemma-box-1-replaced"]),
            run.clone(),
            strings(&["rm", "--force", "lemma-box-1-replaced"]),
        ]
    );
}

/// A stopped container has nothing to keep: removed right before `run`.
#[test]
fn a_stopped_container_is_removed_immediately_before_run() {
    let (_root, service) = swap_service(vec![output(true, ""), output(true, "new-id")]);
    let run = strings(&["run", "--name", "lemma-box-1", "image"]);

    service.replace_and_run("lemma-box-1", &run, false).unwrap();
    assert_eq!(
        service.engine.commands.lock().unwrap().as_slice(),
        [strings(&["rm", "--force", "lemma-box-1"]), run.clone()]
    );
}

/// A newer epoch replaces the running sandbox rather than being refused --
/// that is how the backend moves one to a new image -- while an older one,
/// a caller that lost a race, still is.
#[test]
fn a_newer_epoch_replaces_the_running_sandbox_and_an_older_one_is_refused() {
    let mut asked = workspace_parameters(true);
    let running = |epoch: &str, image: &str| {
        json!({
            "image": image,
            "metadata": {"lemma-epoch": epoch},
            "grants": {"host_access": true, "host_loopback": false},
            "hardening": SANDBOX_HARDENING_VERSION,
            "status": {"status": "RUNNING"},
        })
    };
    asked.metadata = serde_json::from_value(json!({"lemma-epoch": "3"})).unwrap();
    let old_image = "ghcr.io/lemma/workspace@sha256:old";
    assert_eq!(
        existing_container_verdict(&running("2", old_image), &asked),
        ExistingContainer::Replace
    );
    assert_eq!(
        existing_container_verdict(&running("2", &asked.image), &asked),
        ExistingContainer::Replace
    );
    assert_eq!(
        existing_container_verdict(&running("4", old_image), &asked),
        ExistingContainer::Conflict
    );
    assert_eq!(
        existing_container_verdict(&running("3", old_image), &asked),
        ExistingContainer::Conflict,
        "the same epoch on a different image is not a newer generation"
    );
    assert_eq!(
        existing_container_verdict(&running("3", &asked.image), &asked),
        ExistingContainer::Reuse
    );
}

/// A guestd that died mid-replacement left the old container running under
/// `…-replaced`. On the next start it is removed where the new one exists and
/// put back where it does not; and `sandbox.list` never reports one.
#[test]
fn an_interrupted_replacement_is_settled_on_startup_and_never_listed() {
    let (_root, service) = swap_service(vec![
        output(
            true,
            "lemma-sandbox-w-a\nlemma-sandbox-w-a-replaced\nlemma-sandbox-w-b-replaced\n",
        ),
        output(true, ""),
        output(true, ""),
    ]);
    assert_eq!(service.recover_interrupted_replacements().unwrap(), 2);
    let commands = service.engine.commands();
    assert_eq!(
        commands[1],
        strings(&["rm", "--force", "lemma-sandbox-w-a-replaced"])
    );
    assert_eq!(
        commands[2],
        strings(&["rename", "lemma-sandbox-w-b-replaced", "lemma-sandbox-w-b"])
    );

    let (_root, service) = swap_service(vec![output(true, "lemma-sandbox-w-a-replaced\n")]);
    assert_eq!(service.list().unwrap(), json!({"sandboxes": []}));
    assert_eq!(
        service.engine.commands().len(),
        1,
        "the leftover was inspected"
    );
}

/// The token's directory is mounted into the sandbox and owned by its user,
/// so the sandbox can swap the file for a symlink at any moment. Ownership is
/// given by descriptor, never by path, and the open does not follow one.
#[test]
fn the_runtime_token_is_handed_over_by_descriptor_not_by_path() {
    let source = include_str!("../sandbox_run.rs");
    let start = source.find("fn write_runtime_token").unwrap();
    let body = &source[start..];
    let body = &body[..body.find("fn remove_runtime_token").unwrap()];
    assert!(body.contains("libc::fchown(file.as_raw_fd()"), "{body}");
    assert!(body.contains("O_NOFOLLOW"), "{body}");
    assert!(!body.contains("chown(path_bytes"), "{body}");
}
