//! What the workspace is told about a harness.

use super::{
    AdapterSpec, BTreeMap, ChronoDuration, ConfigOption, Digest, HarnessCapabilities,
    HarnessHealth, HarnessSnapshot, HashSet, ResolvedAdapter, SNAPSHOT_TTL, Sha256,
    TRANSIENT_MARKER, Utc, env, executable_search_paths, push_unique,
};

impl ResolvedAdapter {
    #[must_use]
    pub fn args(&self) -> Vec<String> {
        self.spec.args.clone()
    }

    #[must_use]
    pub fn environment(&self) -> BTreeMap<String, String> {
        let mut environment = BTreeMap::new();
        let mut paths = Vec::new();
        let mut seen = HashSet::new();
        for executable in [&self.command, &self.upstream_command] {
            if let Some(parent) = executable.parent() {
                push_unique(&mut paths, &mut seen, parent.to_path_buf());
            }
        }
        for path in executable_search_paths() {
            push_unique(&mut paths, &mut seen, path);
        }
        if let Ok(joined) = env::join_paths(paths) {
            environment.insert("PATH".to_owned(), joined.to_string_lossy().into_owned());
        }
        // Name the upstream binary outright rather than hoping `PATH` order
        // decides it.
        //
        // Prepending the agent's directory above is necessary but not
        // sufficient: an adapter that resolves its agent through `require`
        // rather than `PATH` never sees it, which is exactly what
        // `claude-agent-acp` does. So Lemma probed the version of the agent on
        // this machine, published it, and then ran a vendored copy of a
        // different one -- carrying none of the user's own configuration, which
        // is the entire premise of running agents locally.
        if let Some(variable) = self.spec.upstream_path_env.as_deref() {
            environment.insert(
                variable.to_owned(),
                self.upstream_command.to_string_lossy().into_owned(),
            );
        }
        environment
    }
}

pub(crate) fn snapshot_ready(adapter: &ResolvedAdapter) -> HarnessSnapshot {
    let now = Utc::now();
    let mut snapshot = HarnessSnapshot {
        harness_key: adapter.spec.key.clone(),
        display_name: adapter.spec.display_name.clone(),
        adapter_version: adapter.spec.adapter_version.clone(),
        upstream_version: adapter.upstream_version.clone(),
        health: HarnessHealth::Ready,
        capabilities: HarnessCapabilities {
            plans: true,
            usage: true,
            ..HarnessCapabilities::default()
        },
        // Replaced immediately below, and again by the probe once it lands.
        // `HarnessSnapshot::revision` reads the whole snapshot, so it cannot be
        // computed before there is one.
        config_revision: String::new(),
        config_options: Vec::<ConfigOption>::new(),
        stale_after: now + SNAPSHOT_TTL,
        stale_reason: None,
    };
    snapshot.config_revision = snapshot.revision();
    snapshot
}

/// An adapter whose cache is still being fetched.
///
/// `HarnessHealth::Installing` and the copy for it both already existed; nothing
/// ever produced it, because installing used to finish before anything could
/// look. It goes stale quickly on purpose -- the install is expected to land in
/// the next minute or two, and the point of the state is that it changes.
pub(crate) fn snapshot_installing(spec: &AdapterSpec) -> HarnessSnapshot {
    let now = Utc::now();
    HarnessSnapshot {
        harness_key: spec.key.clone(),
        display_name: spec.display_name.clone(),
        adapter_version: spec.adapter_version.clone(),
        upstream_version: None,
        health: HarnessHealth::Installing,
        capabilities: HarnessCapabilities::default(),
        config_revision: hex::encode(Sha256::digest(b"installing")),
        config_options: Vec::new(),
        stale_after: now + ChronoDuration::seconds(30),
        // No reason. `stale_reason` is rendered underneath the health copy, and
        // the health already *is* "Installing" with a sentence to match — so
        // saying it again just put two lines of the same thing on one row.
        // It carries a reason when the state alone does not explain itself.
        stale_reason: None,
    }
}

pub(crate) fn snapshot_unavailable(spec: &AdapterSpec, reason: &str) -> HarnessSnapshot {
    let now = Utc::now();
    HarnessSnapshot {
        harness_key: spec.key.clone(),
        display_name: spec.display_name.clone(),
        adapter_version: spec.adapter_version.clone(),
        upstream_version: None,
        health: HarnessHealth::ProbeFailed,
        capabilities: HarnessCapabilities::default(),
        config_revision: hex::encode(Sha256::digest(reason.as_bytes())),
        config_options: Vec::new(),
        stale_after: now + ChronoDuration::minutes(5),
        stale_reason: Some(reason.to_owned()),
    }
}

/// Whether this reason describes a moment rather than an installation.
///
/// Read by the poll loop to choose between trying again in seconds and waiting
/// out the ordinary refresh. An agent that is simply not installed must answer
/// `false`, or the host re-probes it forever: on a machine without Cursor that
/// is every refresh for the life of the process.
#[must_use]
pub fn reason_is_transient(reason: &str) -> bool {
    reason.ends_with(TRANSIENT_MARKER)
}

/// The reason with the marker taken off, for anywhere a person will read it.
#[must_use]
pub fn reason_without_marker(reason: &str) -> &str {
    reason.trim_end_matches(TRANSIENT_MARKER)
}
