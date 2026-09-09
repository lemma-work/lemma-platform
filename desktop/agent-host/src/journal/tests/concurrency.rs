//! One busy run must not hold back another.

use super::*;

/// Two runs streaming at once must interleave, not queue behind each other.
///
/// The outbox ordered by `run_id`, which sorts by a UUID: whichever run drew
/// the lexically lower one had its entire backlog drained first, so a busy
/// run could hold back the other's terminal event for a full pass while that
/// conversation showed nothing at all.
#[test]
fn a_busy_run_does_not_hold_back_another_runs_events() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();

    // A second run on the same target, with a deliberately lower id so the
    // old ordering would have put it first regardless of age.
    let mut second_spec = spec.clone();
    second_spec.agent_run_id = Uuid::nil();
    let second = Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::StartRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + ChronoDuration::minutes(1),
        run_id: Some(second_spec.agent_run_id),
        lease_epoch: Some(1),
        payload: serde_json::to_value(&second_spec).unwrap(),
    };
    journal
        .accept_start(target, &second, &second_spec, "codex", "1.0")
        .unwrap();

    // The higher-id run speaks first and often; the lower-id one finishes.
    for _ in 0..40 {
        journal
            .append_event(
                target,
                spec.agent_run_id,
                1,
                EventType::AgentMessageChunk,
                None,
                JsonMap::new(),
            )
            .unwrap();
    }
    journal
        .append_event(
            target,
            second_spec.agent_run_id,
            1,
            EventType::Terminal,
            None,
            JsonMap::new(),
        )
        .unwrap();

    // A pass small enough that the old ordering would have spent all of it
    // on the lexically-lower run's backlog.
    let batches = journal.pending_events(target, 8).unwrap();
    let runs: Vec<Uuid> = batches
        .iter()
        .filter_map(|batch| batch.events.first().map(|event| event.run_id))
        .collect();
    assert!(
        runs.contains(&spec.agent_run_id),
        "the run that spoke first must be delivered first: {runs:?}"
    );
}

/// Interleaved journal writes and reads, in a child process.
///
/// The property is that two threads contending on the journal both make
/// progress. A lock cycle shows up in the first handful of interleavings,
/// not the two hundredth, so the loop is short on purpose: every write
/// commits under `synchronous = FULL`, which is an fsync each, and the cost
/// of those is what separates a developer SSD from a CI runner's virtual
/// disk. At 200 iterations this test spent over 30 seconds on Windows CI
/// and was killed by its own timeout, which then reported a deadlock that
/// was not happening.
const CONCURRENCY_ITERATIONS: usize = 50;

/// Generous because of what it is for. A real deadlock never finishes, so
/// waiting longer only delays a true failure; waiting too little invents
/// one. The child completes in well under a second on a developer machine.
const CONCURRENCY_BUDGET: Duration = Duration::from_secs(120);

#[tokio::test]
async fn concurrent_checkpoints_and_event_reads_make_progress() {
    // A SQLite mutex deadlock cannot be cancelled by a Tokio timeout on
    // the same thread. Isolate the workload so a regression fails promptly
    // and the parent reaps it instead of hanging the entire test job.
    if std::env::var_os("LEMMA_JOURNAL_CONCURRENCY_CHILD").is_none() {
        let thread = std::thread::current();
        let test_name = thread.name().expect("the test runner names its thread");
        // The child records how far it got. Without it a timeout cannot
        // tell "wedged on the third write" from "still going, just slow",
        // and those want opposite fixes.
        let progress = TempDir::new().unwrap();
        let progress_path = progress.path().join("iterations");
        let mut child = tokio::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", test_name, "--nocapture"])
            .env("LEMMA_JOURNAL_CONCURRENCY_CHILD", "1")
            .env("LEMMA_JOURNAL_CONCURRENCY_PROGRESS", &progress_path)
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        if let Ok(status) = tokio::time::timeout(CONCURRENCY_BUDGET, child.wait()).await {
            assert!(status.unwrap().success());
        } else {
            child.kill().await.unwrap();
            let reached = std::fs::read_to_string(&progress_path)
                .ok()
                .and_then(|text| text.trim().parse::<usize>().ok())
                .unwrap_or(0);
            panic!(
                "concurrent journal operations did not finish within \
                 {CONCURRENCY_BUDGET:?}: the writer completed {reached} of \
                 {CONCURRENCY_ITERATIONS} iterations. Stuck near zero is a lock \
                 cycle; most of the way through is a slow disk, and the budget is \
                 what needs changing."
            );
        }
        return;
    }
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    let progress_path = std::env::var_os("LEMMA_JOURNAL_CONCURRENCY_PROGRESS");
    std::thread::scope(|scope| {
        scope.spawn(|| {
            for iteration in 0..CONCURRENCY_ITERATIONS {
                journal
                    .checkpoint(
                        target,
                        spec.agent_run_id,
                        1,
                        RunState::Running,
                        &JsonMap::new(),
                    )
                    .unwrap();
                journal
                    .append_event(
                        target,
                        spec.agent_run_id,
                        1,
                        EventType::AgentMessageChunk,
                        None,
                        JsonMap::new(),
                    )
                    .unwrap();
                // Best effort: this is diagnostics for a failure that has
                // already happened, and a failed write here must not turn a
                // passing run into a failing one.
                if let Some(path) = progress_path.as_ref() {
                    let _ = std::fs::write(path, (iteration + 1).to_string());
                }
            }
        });
        scope.spawn(|| {
            for _ in 0..CONCURRENCY_ITERATIONS {
                journal.pending_events(target, 256).unwrap();
                journal.pending_control(target).unwrap();
            }
        });
        // The delivery task acknowledges while the run streams, so the
        // writer above is not the only thing taking write transactions.
        scope.spawn(|| {
            for _ in 0..200 {
                journal
                    .acknowledge_events(
                        target,
                        &EventAck {
                            run_id: spec.agent_run_id,
                            lease_epoch: 1,
                            acked_through: 0,
                        },
                    )
                    .unwrap();
            }
        });
    });
    assert_eq!(
        journal.pending_events(target, 256).unwrap()[0].events.len(),
        CONCURRENCY_ITERATIONS
    );
}

/// A fast adapter streams faster than the flusher drains, and every one of
/// those chunks is a write transaction competing with the delivery task's
/// reads and acknowledgements. When each of those took its own connection,
/// the five second busy timeout was the only thing between this and a
/// `SQLITE_BUSY` surfacing as `JournalError::Sql` -- which fails the run
/// and loses the turn. It must not merely be unlikely; it must not happen.
#[test]
fn a_fast_stream_never_surfaces_a_busy_journal() {
    const CHUNKS: usize = 600;

    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    let started = std::time::Instant::now();
    std::thread::scope(|scope| {
        scope.spawn(|| {
            for index in 0..CHUNKS {
                let mut payload = JsonMap::new();
                payload.insert("text".into(), format!("chunk {index}").into());
                journal
                    .append_event(
                        target,
                        spec.agent_run_id,
                        1,
                        EventType::AgentMessageChunk,
                        None,
                        payload,
                    )
                    .expect("a streamed chunk must never fail to journal");
            }
        });
        scope.spawn(|| {
            for _ in 0..CHUNKS {
                journal
                    .pending_events(target, 128)
                    .expect("delivery must never fail to read");
            }
        });
    });

    // Summed across batches: a batch caps at 256 events, so this many
    // chunks is deliberately more than one batch's worth.
    let batches = journal.pending_events(target, CHUNKS).unwrap();
    let recorded: usize = batches.iter().map(|batch| batch.events.len()).sum();
    assert_eq!(
        recorded, CHUNKS,
        "every chunk must be recorded exactly once"
    );
    let sequences: Vec<u64> = batches
        .iter()
        .flat_map(|batch| batch.events.iter().map(|event| event.sequence))
        .collect();
    assert!(
        sequences.windows(2).all(|pair| pair[1] == pair[0] + 1),
        "sequences must stay contiguous under concurrent reads"
    );
    // Loose enough not to be a benchmark, tight enough that reintroducing
    // an fsync-per-chunk open/close cycle fails here rather than in a chat.
    assert!(
        started.elapsed() < Duration::from_secs(20),
        "journalling {CHUNKS} chunks took {:?}",
        started.elapsed()
    );
}
