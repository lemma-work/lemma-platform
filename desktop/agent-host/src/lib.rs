//! Lemma Agent Host.
//!
//! The host is the durable local boundary between one or more Lemma targets and
//! user-owned ACP agents. Network commands and event acknowledgements are
//! persisted before side effects so restarts cannot silently repeat prompts.

pub mod acp;
pub mod adapters;
pub mod config;
pub mod conversation_directory;
mod conversation_folders;
/// Running an owner's agent commands on this computer, under Seatbelt.
pub mod host_exec;
pub mod journal;
pub mod link;
pub mod mcp_bridge;
/// The host's end of the MCP bridge. Public for the integration tests, which
/// drive a real bridge process against it.
#[doc(hidden)]
pub mod mcp_relay;
pub mod normalize;
pub mod permissions;
pub mod protocol;
pub mod runtime;
pub mod service;

/// Spawn a child without flashing up a console window.
///
/// The Agent Host runs under locald without a console, and the tools it starts
/// -- npm, and the agent CLIs themselves -- are console programs. Each would
/// otherwise open a console window in the user's face.
///
/// Used where this crate runs a Windows tool directly: removing a service an
/// older release installed, and ending an agent's process tree. Setup commands
/// preserve this flag through their process ownership wrapper instead.
#[cfg(windows)]
pub(crate) trait NoConsoleWindow {
    fn no_console_window(&mut self) -> &mut Self;
}

#[cfg(windows)]
impl NoConsoleWindow for std::process::Command {
    fn no_console_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        self.creation_flags(CREATE_NO_WINDOW)
    }
}

pub const HOST_RELEASE: &str = env!("CARGO_PKG_VERSION");
/// The link protocol this host speaks. 3 is the WebSocket link with
/// normalized run events; 2 was the HTTP long-poll. Lemma closes a link whose
/// `hello` names any other version with 4426, and Desktop's updater takes over.
pub const PROTOCOL_VERSION: u16 = 3;
