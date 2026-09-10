use super::shorten_object_id;

/// The backend stores `object_id` in a 255-character column and refuses a
/// longer one. The host reads that refusal as the run's own fault, replays
/// once, and then discards the transcript -- so a verbose tool-call id from
/// an adapter cost the user their whole conversation turn.
#[test]
fn an_over_long_tool_call_id_cannot_cost_the_user_their_transcript() {
    let long = "call_".to_owned() + &"a".repeat(400);
    let shortened = shorten_object_id(long.clone());

    assert!(
        shortened.len() <= 255,
        "still {} characters, which the backend refuses",
        shortened.len()
    );
    assert!(
        shortened.starts_with("call_"),
        "the id should stay recognisable: {shortened}"
    );
}

/// Truncation alone would collide, and ids sharing a long prefix are
/// exactly what adapters generate. Two different ids must stay different,
/// or two tool calls merge into one in the transcript.
#[test]
fn two_long_ids_that_share_a_prefix_stay_distinct() {
    let prefix = "call_".to_owned() + &"a".repeat(400);
    let one = shorten_object_id(format!("{prefix}-one"));
    let two = shorten_object_id(format!("{prefix}-two"));

    assert_ne!(one, two, "distinct tool calls must not merge");
    assert!(one.len() <= 255 && two.len() <= 255);
}

/// The same id must shorten the same way every time, or an update stops
/// matching the tool call it belongs to.
#[test]
fn shortening_is_stable_for_the_same_id() {
    let id = "call_".to_owned() + &"z".repeat(300);
    assert_eq!(shorten_object_id(id.clone()), shorten_object_id(id));
}

/// An id that is already short is passed through untouched -- the common
/// case, and the one where a surprise would be worst.
#[test]
fn a_normal_id_is_left_exactly_as_it_was() {
    assert_eq!(shorten_object_id("read-project".into()), "read-project");
}

/// Cutting a multi-byte id must not panic on its way to being reported.
#[test]
fn a_multibyte_id_is_cut_on_a_character_boundary() {
    let id = "🧪".repeat(200);
    let shortened = shorten_object_id(id);
    assert!(shortened.len() <= 255);
}
