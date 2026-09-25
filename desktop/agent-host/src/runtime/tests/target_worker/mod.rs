//! The target worker's guards, grouped the way the code they cover is
//! grouped.

// One level deeper than these were, so `super` in the guards below reaches
// the runtime through here rather than directly.
pub(super) use crate::runtime::*;

mod cancellation;
mod control;
mod events;
mod harnesses;
mod runs;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use tokio::sync::{Semaphore, watch};
use uuid::Uuid;

use super::{CANCEL_KILL_AFTER, ProbedHarnesses, TargetWorker, deliver_events};
use crate::acp::{AcpCallbacks, AcpProbeOutcome, AcpRunOutcome, AcpRunRequest, AgentDriver};
use crate::adapters::{AdapterManifest, ResolvedAdapter};
use crate::config::{HostPaths, TargetConfig};
use crate::journal::Journal;
use crate::link::LinkHandle;
use crate::link::protocol::PublishedHarness;
use crate::link::stub::{StubLink, StubState};
use crate::permissions::PermissionDecision;
use crate::protocol::{
    Command, CommandKind, EventType, HostCapacity, HostHello, JsonMap, RunSpec, RunState,
};

pub(super) fn capacity() -> HostCapacity {
    HostCapacity {
        max_runs: 2,
        active_runs: 0,
        available_runs: 2,
    }
}

pub(super) fn cancel_command(run_id: Uuid) -> Command {
    Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::CancelRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + chrono::Duration::minutes(1),
        run_id: Some(run_id),
        lease_epoch: Some(1),
        payload: serde_json::Value::Null,
    }
}

/// A driver that is never asked to do anything; the worker needs one to
/// exist, not to run.
pub(super) struct IdleDriver;

#[async_trait::async_trait]
impl AgentDriver for IdleDriver {
    async fn probe(
        &self,
        _adapter: ResolvedAdapter,
        _scratch_directory: PathBuf,
    ) -> anyhow::Result<AcpProbeOutcome> {
        anyhow::bail!("the flush tests never probe")
    }

    async fn run(
        &self,
        _request: AcpRunRequest,
        _callbacks: Arc<dyn AcpCallbacks>,
    ) -> anyhow::Result<AcpRunOutcome> {
        anyhow::bail!("the flush tests never run an agent")
    }
}

pub(super) struct Harness {
    worker: TargetWorker,
    stub: Arc<StubState>,
    /// A link to the stand-in, already through its handshake, and published
    /// in the worker's slot the way the link loop does.
    link: LinkHandle,
    journal: Journal,
    target_id: Uuid,
    _directory: tempfile::TempDir,
    _shutdown: watch::Sender<bool>,
    /// Held only so the stand-in keeps serving for the harness's lifetime.
    _server: StubLink,
}

impl Harness {
    async fn new() -> Self {
        Self::with_manifest(AdapterManifest::builtin().unwrap()).await
    }

    async fn with_manifest(manifest: AdapterManifest) -> Self {
        let server = StubLink::start().await;
        let stub = Arc::clone(&server.state);
        let directory = tempfile::TempDir::new().unwrap();
        let paths = HostPaths::under(directory.path());
        paths.ensure().unwrap();
        let journal = Journal::open(&paths.journal).unwrap();
        let target_id = Uuid::new_v4();
        let target = TargetConfig {
            target_id,
            name: "stub".into(),
            base_url: server.url.clone(),
            host_id: Uuid::new_v4(),
            user_id: Uuid::new_v4(),
            host_secret: "test-secret".into(),
            enabled: true,
            allow_insecure_http: true,
            draining: false,
            refresh_generation: 0,
        };
        let (shutdown_tx, shutdown_rx) = watch::channel(false);
        let worker = TargetWorker::new(
            target,
            "installation".into(),
            paths,
            journal.clone(),
            manifest,
            Arc::new(IdleDriver),
            PathBuf::from("/nonexistent-bridge"),
            Arc::new(Semaphore::new(2)),
            2,
            shutdown_rx,
            watch::channel(0_u64).1,
        )
        .unwrap();
        let link = crate::link::connect(
            &server.url,
            "test-secret",
            HostHello::current("installation"),
            capacity(),
        )
        .await
        .unwrap()
        .handle;
        worker.slot_owner.set(Some(link.clone()));
        Self {
            worker,
            stub,
            link,
            journal,
            target_id,
            _directory: directory,
            _shutdown: shutdown_tx,
            _server: server,
        }
    }

    /// Journal a run with `count` events, as a live run would.
    fn seed_run(&self, count: u64) -> Uuid {
        let run_id = Uuid::new_v4();
        let spec = RunSpec {
            agent_run_id: run_id,
            conversation_id: Uuid::new_v4(),
            harness_id: Uuid::new_v4(),
            profile_revision: "revision".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: String::new(),
            prompt: vec![serde_json::json!({"type": "text", "text": "hi"})],
            resume_session_id: None,
            workspace_cwd: None,
            context: JsonMap::new(),
            mcp: serde_json::json!({}),
            run_deadline: Utc::now() + chrono::Duration::minutes(5),
            system_prompt_delivery: None,
        };
        let command = Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::StartRun,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(1),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload: serde_json::to_value(&spec).unwrap(),
        };
        self.journal
            .accept_start(self.target_id, &command, &spec, "codex", "1.0")
            .unwrap();
        for _ in 0..count {
            self.journal
                .append_event(
                    self.target_id,
                    run_id,
                    1,
                    EventType::AgentMessageChunk,
                    None,
                    JsonMap::new(),
                )
                .unwrap();
        }
        run_id
    }

    fn accepted(&self) -> HashMap<Uuid, u64> {
        let mut highest = HashMap::new();
        for (run_id, sequence) in self.stub.accepted.lock().unwrap().iter() {
            let entry = highest.entry(*run_id).or_insert(0);
            *entry = (*entry).max(*sequence);
        }
        highest
    }

    /// Every event the journal still owes Lemma for `run_id` -- all of them,
    /// not one delivery pass's worth.
    fn pending(&self, run_id: Uuid) -> Vec<u64> {
        self.journal
            .pending_events(self.target_id, usize::MAX)
            .unwrap()
            .into_iter()
            .flat_map(|batch| batch.events)
            .filter(|event| event.run_id == run_id)
            .map(|event| event.sequence)
            .collect()
    }
}

/// Wait for `predicate`, or fail rather than hang.
pub(super) async fn within(budget: Duration, what: &str, predicate: impl Fn() -> bool) {
    let deadline = tokio::time::Instant::now() + budget;
    while tokio::time::Instant::now() < deadline {
        if predicate() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    panic!("timed out waiting for {what}");
}
