use super::{SETUP_REQUEST_TIMEOUT, before_prompt_deadline};

/// An agent that never answers `initialize` used to hold its capacity
/// permit for the run's whole deadline -- up to an hour -- while the
/// conversation showed nothing. Claude Code waiting on a TTY for onboarding
/// is the shape this has actually taken.
#[tokio::test(start_paused = true)]
async fn a_setup_request_that_never_answers_gives_up_rather_than_holding_the_run() {
    let hangs = std::future::pending::<Result<(), agent_client_protocol::schema::v1::Error>>();

    let outcome = before_prompt_deadline("initialize", hangs).await;

    let error = outcome.expect_err("a request that never answers must not wait for ever");
    let reported = format!("{error:?}");
    assert!(
        reported.contains("initialize"),
        "the message must name which request hung: {reported}"
    );
    assert!(
        reported.contains(&SETUP_REQUEST_TIMEOUT.as_secs().to_string()),
        "and how long it was given: {reported}"
    );
}

/// The deadline must not interfere with an agent that simply answers.
#[tokio::test(start_paused = true)]
async fn an_answer_within_the_deadline_is_passed_straight_through() {
    let answered = async { Ok::<_, agent_client_protocol::schema::v1::Error>(7) };
    assert_eq!(
        before_prompt_deadline("session/new", answered).await.ok(),
        Some(7)
    );
}
