//! One update at a time, and only the one the user agreed to.

use super::*;
use crate::app_update::{offered_is_what_was_agreed, InstallInFlight};

/// A second install is refused while the first is running.
///
/// Two of them are not two updates. They are two downloads of the same bytes
/// racing to replace the same application, each stopping the daemon the other
/// is relying on -- and the button that starts one is a button a person can
/// press twice.
#[test]
fn a_second_installation_is_refused_while_one_is_running() {
    let first = InstallInFlight::claim().expect("nothing is running yet");
    let second = InstallInFlight::claim().err();
    let refusal = second.expect("two installations must not overlap");
    assert!(
        refusal.contains("already being installed"),
        "and the refusal has to say why: {refusal}"
    );

    // However the first one ends -- installed, failed, or the command
    // returning early -- the next attempt is allowed.
    drop(first);
    assert!(
        InstallInFlight::claim().is_ok(),
        "a finished installation must not lock out the next one"
    );
}

/// The user installs the version they were shown, or nothing.
///
/// The command has to ask the feed a second time -- an `Update` is a live
/// handle on a download and does not survive the trip back to the webview --
/// and the feed can answer differently in between: a release published, a bad
/// one pulled. Without this the user agreed to one version and got whichever
/// was being offered a moment later, a downgrade included, with the only sign
/// the version in the restart dialog.
#[test]
fn an_update_that_changed_while_the_user_decided_is_refused() {
    assert!(offered_is_what_was_agreed("0.8.0", "0.8.0").is_ok());

    let moved = offered_is_what_was_agreed("0.8.1", "0.8.0")
        .expect_err("the feed is offering something else now");
    assert!(
        moved.contains("0.8.1") && moved.contains("0.8.0"),
        "{moved}"
    );

    // A downgrade is the same failure and the worse half of it.
    assert!(offered_is_what_was_agreed("0.7.0", "0.8.0").is_err());

    // And nothing at all is not consent. A caller with no version to show did
    // not put one in front of anybody.
    let empty = offered_is_what_was_agreed("0.8.0", "").expect_err("nothing was agreed to");
    assert!(
        empty.contains("Check for updates before installing"),
        "{empty}"
    );
}

/// The restart dialog is waited for off the async runtime.
///
/// `confirm_destructive_action_impl` blocks on a channel until the user
/// answers, and a user may never answer -- so awaiting it inside this async
/// command parked a tokio worker on an open window for as long as it stayed
/// open. Asserted on the source because the property is "this call is not made
/// on the runtime thread", which a test that calls it cannot observe.
#[test]
fn the_restart_dialog_does_not_wait_on_a_runtime_worker() {
    let source = shell_source();
    let body = function_body(&source, "async fn install_app_update(");
    // The call, with its parenthesis. The name also appears in the comment
    // above it explaining this very property, and matching that found a point
    // in the source before the `spawn_blocking` rather than inside it.
    let confirmation = body
        .find("confirm_destructive_action_impl(")
        .expect("it asks before restarting");
    let offloaded = body[..confirmation]
        .rfind("spawn_blocking")
        .expect("the confirmation must be reached through spawn_blocking");
    assert!(
        body[offloaded..confirmation].find("await").is_none(),
        "the spawn_blocking before the confirmation belongs to something else",
    );
}
