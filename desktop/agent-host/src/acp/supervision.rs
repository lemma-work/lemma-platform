//! The agent process: its lifetime, its tree, and its stderr.

use super::{AcpAgent, AcpAgentConfig, ByteStreams, Duration, ResolvedAdapter};

/// How long to wait for a finished agent's exit status and stderr tail.
///
/// Only reached once the protocol is already over, so nothing the user is
/// waiting on sits behind it.
pub(crate) const CHILD_EXIT_GRACE: Duration = Duration::from_secs(5);

/// Bytes of an agent's stderr kept for its failure message.
pub(crate) const STDERR_TAIL_LIMIT: usize = 8 * 1024;

/// An ACP agent process this host supervises itself, rather than letting the
/// protocol library own it.
///
/// The library's own transport races the protocol future against the child's
/// exit. When the exit wins -- which a burst of output followed by an immediate
/// exit reliably produces on a busy machine -- it returns the non-zero status
/// *without* draining what the agent had already written, and the helper it
/// skips is named `await_protocol_shutdown_after_successful_child_exit`. A
/// crash mid-answer therefore discarded the part of the answer the user had
/// already been shown: measured at 405 bytes delivered of 1080 sent.
///
/// Owning the child inverts that order. The protocol reads to stdout EOF, so
/// every notification the agent sent has been dispatched, and only then does
/// the exit status get to explain why the turn ended. See
/// `a_crash_mid_stream_keeps_every_chunk_the_agent_had_already_sent`.
pub(crate) struct SupervisedAgent {
    child: async_process::Child,
    /// Everything this agent starts, held so that dropping this ends all of
    /// it. Windows only, and inert elsewhere: on unix the process group the
    /// protocol crate spawns into already does this.
    ///
    /// `taskkill /T` walks the tree the OS still maintains, which is not the
    /// case that matters. Agents ship behind wrapper launchers -- `npx`,
    /// `uvx` -- and a wrapper that has already exited leaves its child in no
    /// tree at all. That child holds the workspace open, holds the provider
    /// credential and can still write files, after the run it belonged to has
    /// ended. A job object has no such hole.
    ///
    /// `None` when the job could not be created or the child could not be
    /// assigned. Not fatal: `kill_agent_tree` is still what it was, and
    /// refusing to run an agent because a job object was unavailable would
    /// trade a leak for an outage.
    job: Option<lemma_job_object::Job>,
}

impl SupervisedAgent {
    /// Spawn the agent and hand back the transport for its stdio.
    pub(crate) fn spawn(
        agent: &AcpAgent,
    ) -> anyhow::Result<(
        Self,
        ByteStreams<async_process::ChildStdin, async_process::ChildStdout>,
        async_process::ChildStderr,
    )> {
        let (stdin, stdout, stderr, child) = agent
            .spawn_process()
            .map_err(|error| anyhow::anyhow!("could not start the agent: {error}"))?;
        let job = adopt_into_job(&child);
        // `new(outgoing, incoming)`: we write to the child's stdin and read its
        // stdout.
        Ok((Self { child, job }, ByteStreams::new(stdin, stdout), stderr))
    }

    /// Explain a protocol failure using what the process did, now that the
    /// protocol has finished with it.
    ///
    /// Keeps the library's own message shape ("Process exited with {status}:
    /// {stderr}"), because `authentication_hint` and `adapter_failure_message`
    /// both read the agent's own stderr out of this string to tell a signed-out
    /// harness from a crashed one.
    pub(crate) async fn explain(
        &mut self,
        error: &anyhow::Error,
        stderr: tokio::task::JoinHandle<String>,
    ) -> anyhow::Error {
        let status = tokio::time::timeout(CHILD_EXIT_GRACE, self.child.status()).await;
        let Ok(Ok(status)) = status else {
            // Still running, so the protocol ended for its own reason.
            return anyhow::anyhow!("{error}");
        };
        if status.success() {
            return anyhow::anyhow!("{error}");
        }
        let tail = tokio::time::timeout(CHILD_EXIT_GRACE, stderr)
            .await
            .ok()
            .and_then(Result::ok)
            .unwrap_or_default();
        if tail.trim().is_empty() {
            anyhow::anyhow!("Process exited with {status}")
        } else {
            anyhow::anyhow!("Process exited with {status}: {}", tail.trim())
        }
    }
}

impl Drop for SupervisedAgent {
    fn drop(&mut self) {
        kill_agent_tree(self.child.id());
        let _ = self.child.kill();
        // Last, and only on Windows does it do anything: closing the job ends
        // every process in it, including the ones no tree walk can reach.
        drop(self.job.take());
    }
}

/// Put a freshly spawned agent, and everything it goes on to start, in a job.
///
/// Best effort by design. A job object that cannot be created or assigned is
/// a leak we already had; refusing to run the agent over it would trade that
/// leak for an outage.
fn adopt_into_job(child: &async_process::Child) -> Option<lemma_job_object::Job> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;

        let job = lemma_job_object::Job::new().ok()?;
        job.adopt(child.as_raw_handle()).ok()?;
        Some(job)
    }
    #[cfg(not(windows))]
    {
        let _ = child;
        None
    }
}

/// End an agent and everything it started.
///
/// Not just the child. Agents ship behind wrapper launchers -- `npx`, `uvx` --
/// so the process this host holds is usually the wrapper, and the real agent is
/// its child. Killing only the wrapper leaves that agent running: holding the
/// workspace open, holding the provider credential, still able to write files,
/// after the run it belonged to has ended.
///
/// On unix the protocol crate spawns the adapter with `process_group(0)`, so
/// the child leads its own group and one signal reaches all of it.
///
/// Windows had nothing. It sets `CREATE_NO_WINDOW` and no more -- no job
/// object, no new process group -- so `Child::kill` reached exactly one
/// process and the agent behind the wrapper survived every run. `taskkill /T`
/// walks the tree the OS itself maintains, which is the same job done by the
/// tool Windows ships for it. It is best effort, like the signal on unix: a
/// descendant whose parent has already exited is no longer part of any tree to
/// walk, and neither platform can reach one of those.
pub(crate) fn kill_agent_tree(pid: u32) {
    #[cfg(unix)]
    if let Some(pid) = rustix::process::Pid::from_raw(pid.cast_signed()) {
        // ESRCH just means the group is already gone.
        let _ = rustix::process::kill_process_group(pid, rustix::process::Signal::KILL);
    }
    #[cfg(windows)]
    {
        use crate::NoConsoleWindow as _;

        // Spawned and not waited on: this runs in `Drop`, which may be on a
        // runtime thread, and the tree does not need to be gone before the
        // turn is reported. `Child::kill` below still ends the wrapper
        // immediately either way.
        let _ = std::process::Command::new("taskkill")
            .no_console_window()
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
    }
}

/// Read an agent's stderr into a bounded tail, for its failure message.
pub(crate) fn capture_stderr(
    stderr: async_process::ChildStderr,
) -> tokio::task::JoinHandle<String> {
    tokio::spawn(async move {
        use futures_util::AsyncReadExt as _;
        let mut reader = stderr;
        let mut captured = Vec::new();
        let mut buffer = [0_u8; 4096];
        while let Ok(read) = reader.read(&mut buffer).await {
            if read == 0 {
                break;
            }
            tracing::debug!(target = "agent_stderr", bytes = read, "ACP adapter stderr");
            captured.extend_from_slice(&buffer[..read]);
            if captured.len() > STDERR_TAIL_LIMIT {
                let excess = captured.len() - STDERR_TAIL_LIMIT;
                captured.drain(..excess);
            }
        }
        String::from_utf8_lossy(&captured).into_owned()
    })
}

pub(crate) fn build_agent(adapter: &ResolvedAdapter) -> AcpAgent {
    let config = AcpAgentConfig::new(&adapter.command)
        .args(adapter.args())
        .envs(adapter.environment());
    // No `with_debug`: that callback is only consulted by the library's own
    // transport, and this host supervises the process itself (`SupervisedAgent`).
    // Leaving it attached would be dead code that reads as live logging. The
    // stderr half, which is the useful half, is logged in `capture_stderr`.
    AcpAgent::new(config)
}
