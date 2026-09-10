//! The runtime's guards.

// One level deeper than these were: `super` inside each block below used to
// mean the runtime module itself, and now means this one.
use super::*;

mod adapter_failure_message_tests;
mod adapter_installation_tests;
mod capability_tests;
mod command_refusal_tests;
mod harness_publish_scheduling_tests;
mod stream_upsert_tests;
mod target_worker;
