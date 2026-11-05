//! Approvals a run carries from the turn that asked for them.
//!
//! Lemma ends a turn that has waited on a person past its parked window, and a
//! native permission request the turn was holding open ends with it. When the
//! person approves it afterwards, Lemma resumes the conversation with a new
//! run, and its agent -- told the action was approved -- asks for it again.
//! Answering that second request from the first approval is what stops the
//! person being asked twice for one decision.
//!
//! Narrow on purpose. A match is the exact canonical tool and input the person
//! saw on the card, never a prefix or a likeness; each approval allows one
//! request, and only through the agent's own "allow once"; and nothing but an
//! approval is carried, so a denial is never turned into anything.

use std::sync::{Mutex, PoisonError};

use agent_client_protocol::schema::v1::PermissionOption;

use super::{JsonMap, PermissionOptionKind, RunSpec, Value};
use crate::protocol::PreApproval;

/// The approvals one run may still spend, each on one request.
#[derive(Debug, Default)]
pub(crate) struct PreApprovals {
    waiting: Mutex<Vec<PreApproval>>,
}

impl PreApprovals {
    /// The ones in `spec` that approve something.
    pub(crate) fn of(spec: &RunSpec) -> Self {
        let waiting = spec
            .pre_approvals
            .iter()
            .filter(|approval| approval.approves() && !approval.tool_name.trim().is_empty())
            .cloned()
            .collect();
        Self {
            waiting: Mutex::new(waiting),
        }
    }

    /// Spend the approval for exactly this request, if one is waiting.
    ///
    /// `payload` is the permission event's, carrying the gated call as the
    /// normalizer announced it: `tool.name` and `input`, the same canonical
    /// shapes Lemma recorded on the card the person answered. A request whose
    /// agent offers no "allow once" spends nothing: the only answer it could
    /// be given would grant more than one call.
    pub(crate) fn take(
        &self,
        payload: &JsonMap,
        options: &[PermissionOption],
    ) -> Option<PreApproval> {
        if !options
            .iter()
            .any(|option| option.kind == PermissionOptionKind::AllowOnce)
        {
            return None;
        }
        let name = payload
            .get("tool")
            .and_then(|tool| tool.get("name"))
            .and_then(Value::as_str)?;
        let input = payload.get("input")?;
        let mut waiting = self.waiting.lock().unwrap_or_else(PoisonError::into_inner);
        let index = waiting
            .iter()
            .position(|approval| approval.tool_name == name && approval.input == *input)?;
        Some(waiting.remove(index))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agent_client_protocol::schema::v1::PermissionOptionId;
    use serde_json::json;

    fn spec(approvals: &Value) -> RunSpec {
        serde_json::from_value(json!({
            "agent_run_id": uuid::Uuid::nil(),
            "conversation_id": uuid::Uuid::nil(),
            "harness_id": uuid::Uuid::nil(),
            "profile_revision": "r",
            "system_prompt": "",
            "prompt": [],
            "run_deadline": "2026-09-25T00:00:00Z",
            "pre_approvals": approvals,
        }))
        .unwrap()
    }

    fn approval(decision: &str) -> Value {
        json!({
            "approval_id": "agent-host-permission:call-1",
            "tool_name": "exec_command",
            "input": { "command": "lemma --version" },
            "decision": decision,
        })
    }

    fn payload(name: &str, input: &Value) -> JsonMap {
        serde_json::from_value(json!({
            "tool": { "name": name, "source": "native" },
            "input": input,
        }))
        .unwrap()
    }

    fn options(kinds: &[PermissionOptionKind]) -> Vec<PermissionOption> {
        kinds
            .iter()
            .enumerate()
            .map(|(index, kind)| {
                PermissionOption::new(PermissionOptionId::new(format!("o{index}")), "o", *kind)
            })
            .collect()
    }

    #[test]
    fn the_exact_call_is_allowed_once_and_only_once() {
        let approvals = PreApprovals::of(&spec(&json!([approval("APPROVE_ONCE")])));
        let once = options(&[
            PermissionOptionKind::AllowOnce,
            PermissionOptionKind::RejectOnce,
        ]);
        let call = payload("exec_command", &json!({ "command": "lemma --version" }));

        let spent = approvals.take(&call, &once).expect("the approved call");
        assert_eq!(spent.approval_id, "agent-host-permission:call-1");
        assert!(
            approvals.take(&call, &once).is_none(),
            "an approval is spent by the first request it answers"
        );
    }

    #[test]
    fn anything_but_the_exact_call_still_asks() {
        let approvals = PreApprovals::of(&spec(&json!([approval("APPROVE_FOR_SESSION")])));
        let once = options(&[PermissionOptionKind::AllowOnce]);
        for (name, input) in [
            // Another command under the same tool, including one that starts
            // with the approved one.
            (
                "exec_command",
                json!({ "command": "lemma --version; rm -rf ~" }),
            ),
            ("exec_command", json!({ "command": "lemma" })),
            ("exec_command", json!({})),
            // The same input under another tool.
            ("write_file", json!({ "command": "lemma --version" })),
        ] {
            assert!(
                approvals.take(&payload(name, &input), &once).is_none(),
                "{name} {input} was allowed by an approval for something else"
            );
        }
        // And none of those spent it.
        let exact = payload("exec_command", &json!({ "command": "lemma --version" }));
        assert!(approvals.take(&exact, &once).is_some());
    }

    #[test]
    fn a_denial_or_an_unknown_decision_allows_nothing() {
        let call = payload("exec_command", &json!({ "command": "lemma --version" }));
        let once = options(&[PermissionOptionKind::AllowOnce]);
        for decision in ["DENY", "approve_once", "SOMETHING_NEW"] {
            let approvals = PreApprovals::of(&spec(&json!([approval(decision)])));
            assert!(approvals.take(&call, &once).is_none(), "{decision}");
        }
    }

    /// The only way to allow a request that offers no "allow once" grants the
    /// rest of the session, which is more than one approval ever gave.
    #[test]
    fn a_request_offering_only_always_spends_nothing() {
        let approvals = PreApprovals::of(&spec(&json!([approval("APPROVE_ONCE")])));
        let call = payload("exec_command", &json!({ "command": "lemma --version" }));
        assert!(
            approvals
                .take(&call, &options(&[PermissionOptionKind::AllowAlways]))
                .is_none()
        );
        assert!(
            approvals
                .take(&call, &options(&[PermissionOptionKind::AllowOnce]))
                .is_some()
        );
    }

    #[test]
    fn a_request_whose_call_was_never_announced_still_asks() {
        let approvals = PreApprovals::of(&spec(&json!([approval("APPROVE_ONCE")])));
        let once = options(&[PermissionOptionKind::AllowOnce]);
        assert!(approvals.take(&JsonMap::new(), &once).is_none());
    }

    #[test]
    fn a_spec_from_before_pre_approvals_carries_none() {
        let spec: RunSpec = serde_json::from_value(json!({
            "agent_run_id": uuid::Uuid::nil(),
            "conversation_id": uuid::Uuid::nil(),
            "harness_id": uuid::Uuid::nil(),
            "profile_revision": "r",
            "system_prompt": "",
            "prompt": [],
            "run_deadline": "2026-09-25T00:00:00Z",
        }))
        .unwrap();
        assert!(spec.pre_approvals.is_empty());
    }
}
