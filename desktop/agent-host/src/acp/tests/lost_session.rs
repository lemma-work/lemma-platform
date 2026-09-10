//! A conversation the provider no longer has.

use super::session::{spec_delivered_once, spec_resuming};
use super::*;

/// The turn that is missing its own conversation is told so.
///
/// Lemma leaves the history out of the prompt exactly when it expects
/// `session/load` to supply it, so a load that failed leaves the agent holding
/// this turn's words and nothing else. Told nothing, it answers as though it
/// remembers -- the user gets a confident reply to a question that referred to
/// something the agent has never seen, and neither of them has any sign that
/// anything went wrong.
#[test]
fn a_recovered_turn_tells_the_agent_the_conversation_could_not_be_recovered() {
    let recovered = render_prompt(&spec_delivered_once(), SessionOrigin::Recovered);
    assert!(
        recovered.contains("could not be recovered"),
        "the agent has to know it is missing the conversation: {recovered}"
    );
    assert!(
        recovered.contains("ask for what you need"),
        "and what to do about it: {recovered}"
    );

    // Every other origin is unchanged. A new conversation is not a recovered
    // one -- there is nothing missing from it -- and a loaded session has its
    // history.
    for origin in [SessionOrigin::New, SessionOrigin::Loaded] {
        let rendered = render_prompt(&spec_delivered_once(), origin);
        assert!(
            !rendered.contains("could not be recovered"),
            "{origin:?} is not missing anything: {rendered}"
        );
    }
}

/// Order matters: instructions, then what is missing, then the user.
///
/// The note is a fact about this turn rather than part of who the agent is, so
/// it goes after the system framing; and it has to precede the message it is
/// about, or the agent reads the question before it is told what it lacks.
#[test]
fn the_note_sits_between_the_instructions_and_the_user() {
    let rendered = render_prompt(&spec_delivered_once(), SessionOrigin::Recovered);
    let system = rendered
        .find("<system>")
        .expect("a fresh session is instructed");
    let note = rendered
        .find("<conversation-recovered>")
        .expect("the note is rendered");
    let user = rendered
        .find("Hello")
        .expect("the user's words are rendered");
    assert!(system < note && note < user, "out of order: {rendered}");
}

/// A recovered session is a fresh one, so it has never seen the instructions.
///
/// It reaches this through a different branch than `New` does, and getting it
/// wrong produces the worst outcome available: a turn with neither history nor
/// instructions.
#[test]
fn a_recovered_session_still_gets_its_instructions() {
    assert!(sends_instructions(
        &spec_delivered_once(),
        SessionOrigin::Recovered
    ));
    assert!(SessionOrigin::Recovered.is_fresh());
    assert_eq!(SessionOrigin::Recovered.as_str(), "recovered");
}

/// An image-only turn on a recovered session still says what is missing.
///
/// The system block is conditional, so a turn can render to nothing; this one
/// must not, because the one thing it has to carry is the note.
#[test]
fn a_recovered_turn_with_no_text_still_carries_the_note() {
    let mut spec = spec_resuming(Some("sess-1"));
    spec.system_prompt = String::new();
    spec.prompt = vec![serde_json::json!({
        "type": "image",
        "data": "AA==",
        "mimeType": "image/png",
    })];
    let blocks = prompt_blocks(&spec, SessionOrigin::Recovered);
    let text = blocks.iter().find_map(|block| match block {
        ContentBlock::Text(text) => Some(text.text.clone()),
        _ => None,
    });
    assert!(
        text.is_some_and(|text| text.contains("could not be recovered")),
        "an image-only recovered turn still has to say what it lost"
    );
}

/// And Lemma is told, not just the agent.
///
/// It used to be a `warn!` on the machine that noticed, which is nowhere a
/// person looks: nothing distinguished a reply that started from nothing from
/// an ordinary one.
#[test]
fn lemma_is_told_which_session_was_lost() {
    let payload = session_lost_payload("rollout-42");
    assert_eq!(payload["status"], "session_lost");
    assert_eq!(payload["requested_session"], "rollout-42");
    assert!(
        payload["detail"]
            .as_str()
            .is_some_and(|detail| detail.contains("no longer has the session")),
        "the detail is what a person reads: {payload:?}"
    );
}
