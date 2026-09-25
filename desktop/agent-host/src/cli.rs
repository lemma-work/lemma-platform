//! What the command line accepts.

use clap::{Parser, Subcommand};
use std::path::PathBuf;
use url::Url;
use uuid::Uuid;

#[derive(Parser)]
#[command(name = "lemma-agent-host", version, about)]
pub(crate) struct Cli {
    /// Override the platform Agent Host data directory.
    #[arg(long, global = true, env = "LEMMA_AGENT_HOST_DATA_DIR")]
    pub(crate) data_dir: Option<PathBuf>,

    #[command(subcommand)]
    pub(crate) command: Command,
}

#[derive(Subcommand)]
pub(crate) enum Command {
    /// Run all configured target connections until interrupted.
    Serve,
    /// Pair this machine with a Lemma target using a one-time code.
    Connect {
        #[arg(long)]
        url: Url,
        // URL-safe random codes can begin with a hyphen.
        #[arg(long, allow_hyphen_values = true)]
        pairing_code: String,
        #[arg(long, default_value = "My computer")]
        name: String,
        /// Permit plain HTTP only when the URL is loopback.
        #[arg(long)]
        allow_insecure_http: bool,
    },
    /// Show service, target connectivity, and durable queue state.
    #[command(alias = "list")]
    Status {
        #[arg(long)]
        json: bool,
    },
    /// Revoke a target connection and remove its local device identity.
    Disconnect {
        /// Target UUID or exact configured name. Optional when only one exists.
        #[arg(long)]
        target: Option<String>,
        /// Remove local state even if the remote revocation request fails.
        #[arg(long)]
        force_local: bool,
    },
    /// Stop accepting new runs while allowing active turns to finish.
    Drain {
        #[arg(long)]
        target: Option<String>,
    },
    /// Resume accepting runs after a drain.
    Resume {
        #[arg(long)]
        target: Option<String>,
    },
    /// Force an ACP capability/model/config refresh.
    Refresh {
        #[arg(long)]
        target: Option<String>,
    },
    /// Print the local Agent Host log.
    Logs {
        #[arg(long, default_value_t = 200)]
        lines: usize,
        #[arg(short, long)]
        follow: bool,
    },
    /// Remove a per-user service installed by an older release.
    ///
    /// Nothing installs one any more -- Desktop owns the Agent Host's
    /// lifecycle -- but a machine that ran `install-service` before still has
    /// one, and this is how it goes away. `status` reports whether there is
    /// one to remove.
    UninstallService,
    /// Discover installed certified agents without contacting Lemma.
    Discover {
        #[arg(long)]
        json: bool,
        /// Launch every ready adapter and report live ACP capabilities/config.
        #[arg(long)]
        probe: bool,
    },
    /// Validate configuration, journal integrity, credentials, and adapters.
    Doctor {
        #[arg(long)]
        json: bool,
        /// Reinstall missing or tampered pinned adapters.
        #[arg(long)]
        repair: bool,
    },
    /// Run a direct local ACP smoke prompt. No Lemma tools are injected.
    Run {
        #[arg(long)]
        agent: String,
        #[arg(long)]
        prompt: String,
        /// Emit the ACP session, every streamed event, and the terminal outcome as NDJSON.
        #[arg(long)]
        json: bool,
    },
    /// Turn running Lemma agents' commands on this computer on or off.
    HostExecution {
        #[command(subcommand)]
        action: HostExecutionAction,
    },
    /// Internal: host execution's worker, speaking JSON lines on stdio. The
    /// Agent Host starts one per open workspace under `sandbox-exec`; run by
    /// hand only to debug it (see the README).
    #[command(hide = true)]
    ExecServer {
        /// Default workspace roots go under `<root-base>/c/<date>/<slug>`.
        #[arg(long)]
        root_base: PathBuf,
    },
    /// Internal run-scoped stdio MCP bridge used by ACP adapters.
    #[command(hide = true)]
    McpBridge {
        #[arg(long)]
        target_id: Uuid,
        #[arg(long)]
        run_id: Uuid,
    },
}

#[derive(Subcommand)]
pub(crate) enum HostExecutionAction {
    /// Let the owner's Lemma agents run commands here, under Seatbelt.
    Enable,
    /// Stop them, and stop every command they are running.
    Disable,
    /// Show the setting and whether this computer supports it.
    Status {
        #[arg(long)]
        json: bool,
    },
    /// Take a fresh snapshot of the login shell's environment, for when the
    /// owner has changed their shell profile.
    RefreshEnvironment,
}
