//! Lemma Agent Host.
//!
//! The host is the durable local boundary between one or more Lemma targets and
//! user-owned ACP agents. Network commands and event acknowledgements are
//! persisted before side effects so restarts cannot silently repeat prompts.

pub mod acp;
pub mod adapters;
pub mod api;
pub mod config;
pub mod journal;
pub mod mcp_bridge;
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
/// A no-op everywhere else, so call sites stay platform-neutral.
pub(crate) trait NoConsoleWindow {
    fn no_console_window(&mut self) -> &mut Self;
}

impl NoConsoleWindow for std::process::Command {
    #[cfg(windows)]
    fn no_console_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        self.creation_flags(CREATE_NO_WINDOW)
    }

    #[cfg(not(windows))]
    fn no_console_window(&mut self) -> &mut Self {
        self
    }
}

pub const HOST_RELEASE: &str = env!("CARGO_PKG_VERSION");
pub const PROTOCOL_VERSION: u16 = 2;
