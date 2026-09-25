//! Host execution: an owner's agent commands, run on this Mac under Seatbelt.
//!
//! Lemma sends `op` frames on the link; the relay hands each to an
//! exec-server -- this same binary, `lemma-agent-host exec-server`, started
//! under `sandbox-exec` for one workspace -- and sends the answer back. See
//! docs/architecture/desktop-host-execution.md.

pub mod env;
#[cfg(unix)]
mod files;
pub mod paths;
#[cfg(unix)]
mod process;
#[cfg(unix)]
pub mod relay;
pub mod ring;
#[cfg(unix)]
pub mod roots;
pub mod seatbelt;
#[cfg(unix)]
pub mod server;
pub mod wire;

#[cfg(all(test, unix))]
mod tests;
