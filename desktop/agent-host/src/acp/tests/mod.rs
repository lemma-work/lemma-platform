//! The ACP layer's guards.

// One level deeper than these were: `super` inside each block below used to
// mean the acp module itself, and now means this one.
use super::*;

mod lost_session;
mod object_id_tests;
mod scoped_mcp_approval_tests;
mod session;
mod setup_deadline_tests;
