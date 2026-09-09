//! Which harnesses this computer has, and publishing them.

use super::{
    Arc, ConfigOption, Duration, FIRST_HARNESS_WAIT, HARNESS_REFRESH_INTERVAL,
    HARNESS_RETRY_INTERVAL, HarnessCapabilities, HarnessHealth, HarnessSnapshot, HashMap, Ordering,
    OwnedTask, ProbedHarness, ProbedHarnesses, TargetWorker, Value, redact_error,
};

/// Enough of a revision to correlate two log lines, without the other 56 chars.
///
/// Revisions are only ever compared for equality, and a reader tracing a run
/// that was rejected for naming the wrong one needs to see *that* they differ,
/// not which bytes.
pub(crate) fn short_revision(revision: &str) -> &str {
    &revision[..revision.len().min(8)]
}

/// How many models a probe came back with, for the probe log line.
///
/// The interesting failure is a harness that probes fine and offers nothing:
/// that is what leaves a saved model unselectable and a run rejected for a
/// model "the harness does not offer".
pub(crate) fn model_option_count(options: &[ConfigOption]) -> usize {
    options
        .iter()
        .filter(|option| option.category == "model")
        .map(|option| option.options.len())
        .sum()
}

pub(crate) fn capabilities_from_acp(value: &Value) -> HarnessCapabilities {
    HarnessCapabilities {
        load_session: value.get("loadSession") == Some(&Value::Bool(true)),
        resume_session: value.pointer("/sessionCapabilities/resume").is_some(),
        close_session: value.pointer("/sessionCapabilities/close").is_some(),
        images: value.pointer("/promptCapabilities/image") == Some(&Value::Bool(true)),
        plans: true,
        usage: true,
        // Session loading/resume can support cross-turn continuity, but ACP
        // does not provide a durable fence proving an in-flight prompt is safe
        // to replay after a crash.
        durable_session_recovery: false,
    }
}

/// Turn an adapter's failure into something the person reading it can act on.
///
/// A coding agent that is installed but not signed in is the single most common
/// way a local run fails, and what it produces is the agent's own internal
/// error — for Claude Code, `Internal error: Failed to authenticate: OAuth
/// session expired and could not be refreshed: {"errorKind":
/// "authentication_failed"}`. That is accurate and useless: it names no agent,
/// suggests nothing to do, and reads like a defect in Lemma rather than a
/// session that needs renewing.
///
/// Returns `None` when the failure is not recognisably an authentication one,
/// so anything unfamiliar still travels verbatim rather than being flattened
/// into a guess.
pub(crate) fn authentication_hint(harness: &str, error: &str) -> Option<String> {
    let normalized = error.to_ascii_lowercase();
    let looks_like_auth = normalized.contains("authentication_failed")
        || normalized.contains("failed to authenticate")
        || normalized.contains("oauth session expired")
        || normalized.contains("not logged in")
        || normalized.contains("not authenticated")
        || (normalized.contains("unauthorized") && normalized.contains("session"));
    if !looks_like_auth {
        return None;
    }
    // "…and send the message again" was wrong, and wrong in a way that cost
    // people a restart: a signed-out harness is published AUTH_REQUIRED, and
    // admission refuses every run against it until the next probe — up to
    // fifteen minutes away. Sending again does nothing for that whole window.
    // What does work is asking the host to look again, which is what the
    // "Re-check" action on the failure does.
    Some(format!(
        "{harness} is installed on this computer but not signed in. \
         Sign in to it in a terminal, then press Re-check. \
         Lemma runs it with your credentials and never sees them."
    ))
}

/// Frame an adapter failure so it says which agent, and where to look.
///
/// Adapters report their own internals and the Agent Host forwarded them
/// untouched: `Internal error: OpenCode service failure: {"service":
/// "session"}` names no agent, points nowhere, and reads like a defect in
/// Lemma. This does not try to interpret the failure — guessing would bury the
/// one line that explains it — it just says whose failure it is and where the
/// detail lives.
///
/// Returns `None` for anything that is not an adapter's internal error, so
/// ordinary messages ("run deadline elapsed") stay exactly as they are.
///
/// An agent that simply dies is the same thing to a person: it went wrong on
/// their computer and the place to find out why is a terminal. It did not read
/// as one here, because it does not report itself -- there is no JSON-RPC
/// error to forward, only an exit status. Since this host took ownership of the
/// child process, that arrives as "Process exited with ...", and a crash
/// mid-answer was framed as a bare failure with nothing to act on. The browser
/// journey that drives a crashing adapter is what noticed.
pub(crate) fn adapter_failure_message(harness: &str, error: &str) -> Option<String> {
    let normalized = error.to_ascii_lowercase();
    let is_adapter_internal = normalized.contains("internal error")
        || normalized.contains("service failure")
        || normalized.contains("\"service\"")
        || normalized.starts_with("process exited with");
    if !is_adapter_internal {
        return None;
    }
    Some(format!(
        "{harness} encountered an error on this computer. \
         Check that it runs on its own in a terminal, then try again. \
         Its own error was: {}",
        error.trim()
    ))
}

impl TargetWorker {
    /// Bring the published harnesses up to date, then wait if there are none.
    ///
    /// Two jobs, and the first is the one that is easy to miss. Draining used
    /// to happen only at the top of a loop iteration, and commands are handled
    /// in the *same* iteration that polled them — so a publish that landed
    /// while the poll was open sat unread in the channel until the next
    /// iteration, and `handle_start` compared each command against the
    /// harnesses this host held before it. Lemma mints commands against the
    /// revision it was just told, which is precisely the one still in the
    /// channel, so a run naming the newest revision was rejected as superseded
    /// by an older one, two seconds after the publish that made it current.
    ///
    /// The second job is the original one: the first commands can arrive
    /// before the first publish, and rejecting those as `HARNESS_NOT_FOUND` is
    /// permanent and wrong — the machine has the agent, it just has not said
    /// so yet.
    ///
    /// Only ever called when a command is in hand: the heartbeat must never
    /// wait on discovery, which is the whole reason publishing moved off the
    /// poll path.
    pub(crate) async fn sync_harnesses_for_commands(&mut self) {
        self.drain_published();
        if !self.harnesses.is_empty() {
            return;
        }
        // Ask, rather than only wait. Arriving here means a command needs a
        // harness we have not published, which is exactly the moment to try
        // again — waiting alone would sit out whatever remains of the retry or
        // refresh interval and then reject the command for nothing.
        self.refresh_harnesses();
        self.refresh_due = std::time::Instant::now() + HARNESS_REFRESH_INTERVAL;

        let deadline = tokio::time::Instant::now() + FIRST_HARNESS_WAIT;
        while self.harnesses.is_empty() {
            match tokio::time::timeout_at(deadline, self.probed.1.recv()).await {
                Ok(Some(Some(published))) => self.store_published(published),
                // A publish failed while we were waiting. Retry within the
                // deadline we are already holding rather than giving up on it.
                Ok(Some(None)) => {
                    self.refresh_due = std::time::Instant::now() + HARNESS_RETRY_INTERVAL;
                    tokio::time::sleep(HARNESS_RETRY_INTERVAL).await;
                    self.refresh_harnesses();
                }
                Ok(None) => return,
                Err(_) => {
                    tracing::warn!("no harnesses published yet; the command will be rejected");
                    return;
                }
            }
        }
    }

    /// Publish what this machine can run, cheaply first and fully second.
    ///
    /// This is the first thing the worker loop does, and until it returns the
    /// host has not polled once - so it has no heartbeat, the workspace reports
    /// it OFFLINE, and creating a profile against it is refused. Probing is
    /// what makes that slow: every probe spawns the agent, runs an ACP
    /// `initialize` and `session/new`, and waits up to 20s. Serially, over four
    /// adapters with one that times out, a cold start took 47s to first
    /// heartbeat, measured.
    ///
    /// So the cheap half - which adapters exist and resolve - is published on
    /// its own first, and the probes then run concurrently rather than one
    /// after another. The machine appears with its agents almost immediately;
    /// their config options arrive a moment later.
    pub(crate) fn refresh_harnesses(&mut self) {
        if self
            .probe_task
            .as_ref()
            .is_some_and(|task| !task.is_finished())
        {
            // Coalesce refresh requests while a probe is still running. A
            // changed installation is checked once more after it completes.
            self.reprobe_requested.store(true, Ordering::SeqCst);
            return;
        }
        let client = self.client.clone();
        let sender = self.probed.0.clone();
        // Everything the spawned work needs, taken before the task is built:
        // it outlives this borrow of `self`.
        let manifest = self.manifest.clone();
        let driver = Arc::clone(&self.driver);
        let probe_root = self.paths.root.join("probe");
        let discover_manifest = manifest.clone();
        // What is published right now, so a probe can say whether it actually
        // changed anything. The obvious comparison — against the revision the
        // snapshot arrived with — is not that: discovery has not run the ACP
        // probe yet, so its snapshot carries no `config_options` and hashes to
        // something the host has never published. Every probe therefore
        // reported `revision_changed=true`, including the three consecutive
        // ones that produced a byte-identical `opencode@4a03aaa4`.
        let published_revisions: HashMap<String, String> = self
            .harnesses
            .values()
            .map(|harness| (harness.harness_key.clone(), harness.config_revision.clone()))
            .collect();
        let build_probes = move |discovered: Vec<HarnessSnapshot>| {
            discovered.into_iter().map(move |mut snapshot| {
                let manifest = manifest.clone();
                let driver = Arc::clone(&driver);
                let scratch = probe_root.join(&snapshot.harness_key);
                let published_revision = published_revisions.get(&snapshot.harness_key).cloned();
                async move {
                    if snapshot.health != HarnessHealth::Ready {
                        return snapshot;
                    }
                    let Ok(adapter) = manifest.resolve(&snapshot.harness_key) else {
                        tracing::info!(
                            harness = %snapshot.harness_key,
                            outcome = "unresolved",
                            "harness probe skipped"
                        );
                        return snapshot;
                    };
                    let started = std::time::Instant::now();
                    match tokio::time::timeout(
                        Duration::from_secs(20),
                        driver.probe(adapter, scratch),
                    )
                    .await
                    {
                        Ok(Ok(probe)) => {
                            snapshot.config_options = probe.config_options;
                            snapshot.capabilities = capabilities_from_acp(&probe.capabilities);
                            snapshot.config_revision = snapshot.revision();
                            tracing::info!(
                                harness = %snapshot.harness_key,
                                outcome = "ready",
                                elapsed_ms = started.elapsed().as_millis(),
                                models = model_option_count(&snapshot.config_options),
                                revision_changed = published_revision
                                    .as_deref()
                                    .is_none_or(|published| {
                                        published != snapshot.config_revision
                                    }),
                                revision = %short_revision(&snapshot.config_revision),
                                auth_methods = %probe.auth_methods,
                                "harness probe finished"
                            );
                        }
                        Ok(Err(error)) => {
                            let raw = error.to_string();
                            // An agent that is installed but not signed in is a
                            // different thing from one that could not start, and
                            // the workspace already knows how to say so —
                            // "Sign-in needed", with the fix. It just never got
                            // told, because every probe failure looked alike.
                            if let Some(hint) = authentication_hint(&snapshot.display_name, &raw) {
                                snapshot.health = HarnessHealth::AuthRequired;
                                snapshot.stale_reason = Some(hint);
                            } else {
                                snapshot.health = HarnessHealth::ProbeFailed;
                                snapshot.stale_reason = Some(redact_error(&raw));
                            }
                            tracing::info!(
                                harness = %snapshot.harness_key,
                                outcome = ?snapshot.health,
                                elapsed_ms = started.elapsed().as_millis(),
                                detail = %redact_error(&raw),
                                "harness probe finished"
                            );
                        }
                        Err(_) => {
                            snapshot.health = HarnessHealth::ProbeFailed;
                            snapshot.stale_reason = Some("ACP probe timed out".to_owned());
                            tracing::info!(
                                harness = %snapshot.harness_key,
                                outcome = "timeout",
                                elapsed_ms = started.elapsed().as_millis(),
                                "harness probe finished"
                            );
                        }
                    }
                    snapshot
                }
            })
        };
        // Spawned, never awaited. Discovery runs each adapter's binary just to
        // read its version, and probing then opens a whole ACP session per
        // adapter. Doing either before the first poll left the host with no
        // heartbeat for 47 seconds, so the workspace called a working machine
        // OFFLINE and refused to bind a profile to it.
        //
        // Published exactly once, after probing. An earlier revision published
        // the unprobed snapshots first so the agents would appear sooner — but
        // an unprobed snapshot has no `config_options`, and publishing it
        // *replaced* the probed ones. Every saved `config_selections` key then
        // failed validation as "unknown configuration selection". Getting the
        // machine online is what the poll does; the harnesses can wait for
        // their probe.
        self.probe_task = Some(OwnedTask(tokio::spawn(async move {
            let discovered = discover_manifest.discover();
            let enriched = futures_util::future::join_all(build_probes(discovered)).await;
            let probes = enriched
                .iter()
                .map(|snapshot| {
                    (
                        snapshot.harness_key.clone(),
                        ProbedHarness {
                            capabilities: snapshot.capabilities.clone(),
                            config_options: snapshot.config_options.clone(),
                        },
                    )
                })
                .collect();
            // Logged before the request, and again with its outcome, because a
            // publish that never returns is indistinguishable in a log from one
            // that was never attempted -- and "was this host even trying?" is
            // the first question asked of a machine whose agents never answer.
            let attempted = enriched
                .iter()
                .map(|snapshot| {
                    format!(
                        "{}={:?}@{}",
                        snapshot.harness_key,
                        snapshot.health,
                        short_revision(&snapshot.config_revision)
                    )
                })
                .collect::<Vec<_>>()
                .join(" ");
            // Read before the marker is stripped, and before `enriched` is
            // handed to the publish that consumes it.
            let retry_soon = enriched.iter().any(|snapshot| {
                snapshot
                    .stale_reason
                    .as_deref()
                    .is_some_and(crate::adapters::reason_is_transient)
            });
            let enriched = enriched
                .into_iter()
                .map(|mut snapshot| {
                    if let Some(reason) = snapshot.stale_reason.take() {
                        snapshot.stale_reason =
                            Some(crate::adapters::reason_without_marker(&reason).to_owned());
                    }
                    snapshot
                })
                .collect::<Vec<_>>();
            tracing::info!(harnesses = %attempted, retry_soon, "publishing probed harnesses");
            match client.publish_harnesses(enriched).await {
                Ok(published) => {
                    let accepted = published
                        .iter()
                        .map(|harness| {
                            format!(
                                "{}@{}",
                                harness.harness_key,
                                short_revision(&harness.config_revision)
                            )
                        })
                        .collect::<Vec<_>>()
                        .join(" ");
                    tracing::info!(harnesses = %accepted, "published probed harnesses");
                    let _ = sender.send(Some(ProbedHarnesses {
                        published,
                        probes,
                        retry_soon,
                    }));
                }
                Err(error) => {
                    tracing::warn!(%error, "publishing probed harnesses failed");
                    // Tell the loop, so it can try again soon. Without this the
                    // next attempt is a full refresh interval away and every
                    // command in between is rejected for referencing a harness
                    // this host never got to publish.
                    let _ = sender.send(None);
                }
            }
        })));
    }

    pub(crate) fn store_published(&mut self, probed: ProbedHarnesses) {
        self.harnesses = probed
            .published
            .into_iter()
            .map(|harness| (harness.id, harness))
            .collect();
        self.probes = probed.probes;
    }
}
