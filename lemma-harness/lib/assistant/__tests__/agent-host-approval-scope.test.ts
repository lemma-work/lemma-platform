import { describe, expect, it } from "vitest";
import { userApprovalDetails } from "@/components/lemma/assistant/assistant-approval-cards";

/**
 * An Agent Host permission is answered by choosing one of the *local agent's*
 * own options, not by a scope Lemma invents. The agent states that scope in the
 * option's name — "Always Allow WebFetch(domain:github.com)" grants far less
 * than "Always Allow all Bash" — so the card has to show the agent's wording
 * rather than a generic label the user cannot judge.
 */
function agentHostArgs(options: Array<Record<string, unknown>>) {
  return {
    title: "Fetch https://github.com/lemma-work/lemma-platform",
    reason: "The local agent asked for permission to use a native tool.",
    tool_name: "fetch",
    agent_host_permission: { request_id: "toolu_01", options },
  };
}

describe("Agent Host approval scope", () => {
  it("labels the session button with the agent's own always-allow option", () => {
    const details = userApprovalDetails(agentHostArgs([
      { option_id: "allow_always", kind: "allowalways", name: "Always Allow WebFetch(domain:github.com)" },
      { option_id: "allow", kind: "allowonce", name: "Allow" },
      { option_id: "reject", kind: "rejectonce", name: "Reject" },
    ]));

    expect(details.canApproveForSession).toBe(true);
    expect(details.approveForSessionLabel).toBe("Always Allow WebFetch(domain:github.com)");
  });

  it("accepts however the agent spells the kind", () => {
    for (const kind of ["allow_always", "allowAlways", "ALLOW_ALWAYS"]) {
      const details = userApprovalDetails(agentHostArgs([
        { option_id: "allow_always", kind, name: "Always Allow all Bash" },
      ]));
      expect(details.approveForSessionLabel).toBe("Always Allow all Bash");
    }
  });

  it("hides the session button when the agent offered no always option", () => {
    // Without an always option the decision falls back to allow-once, so
    // offering "for session" would promise a grant that expires on this call.
    const details = userApprovalDetails(agentHostArgs([
      { option_id: "allow", kind: "allowonce", name: "Allow" },
      { option_id: "reject", kind: "rejectonce", name: "Reject" },
    ]));

    expect(details.canApproveForSession).toBe(false);
    expect(details.approveForSessionLabel).toBeUndefined();
  });

  it("leaves ordinary pod-agent approvals alone", () => {
    // Those are scoped by Lemma's own session-approval store, which does not
    // depend on any option list, so the button stays and keeps its wording.
    const details = userApprovalDetails({
      title: "Delete table",
      reason: "The assistant wants to delete a table.",
      tool_name: "table.delete",
    });

    expect(details.canApproveForSession).toBe(true);
    expect(details.approveForSessionLabel).toBeUndefined();
  });
});
