import { describe, expect, it } from "vitest";

import {
  codingAgentStarterPrompt,
  defaultWorkspaceName,
  generatedOrganizationName,
  hasUsableProfileName,
  nextTeamSetupStep,
  normalizeOnboardingStep,
  podNameForAudience,
  previousOnboardingStep,
  resolveOnboardingStartStep,
  setupStepsForAudience,
  startPathLaunchConfig,
} from "@/components/onboarding/account-onboarding-helpers";
import { workDomainFromEmail } from "@/lib/utils/organization-slugs";

describe("onboarding step paths", () => {
  it("routes team onboarding through workspace selection", () => {
    expect(setupStepsForAudience("team")).toEqual([
      "boot",
      "identity",
      "audience",
      "workspace",
      "team",
      "connect",
      "start",
    ]);
  });

  it("keeps workspace and team pod creation as separate transitions", () => {
    expect(
      nextTeamSetupStep({ hasOrganization: false, hasPod: false }),
    ).toBe("workspace");
    expect(
      nextTeamSetupStep({ hasOrganization: true, hasPod: false }),
    ).toBe("team");
    expect(
      nextTeamSetupStep({ hasOrganization: true, hasPod: true }),
    ).toBe("connect");
  });

  it("uses the team label only for the pod name", () => {
    expect(podNameForAudience("team", "Sales")).toBe("Sales Pod");
  });

  it("defaults team workspaces from non-public email domains", () => {
    expect(
      defaultWorkspaceName(
        "Ada Lovelace",
        workDomainFromEmail("ada@gappy.ai"),
      ),
    ).toBe("Gappy Workspace");
    expect(
      defaultWorkspaceName(
        "Ada Lovelace",
        workDomainFromEmail("ada@research.acme.co.uk"),
      ),
    ).toBe("Acme Workspace");
  });

  it("keeps the user-name fallback for public email providers", () => {
    expect(
      defaultWorkspaceName(
        "Ada Lovelace",
        workDomainFromEmail("ada@gmail.com"),
      ),
    ).toBe("Ada's Workspace");
  });

  it("moves old team-first drafts back to workspace setup", () => {
    expect(normalizeOnboardingStep("team", "team", false)).toBe("workspace");
    expect(normalizeOnboardingStep("team", "team", true)).toBe("team");
  });

  it("collects identity before direct personal onboarding reaches first value", () => {
    expect(setupStepsForAudience("personal")).toEqual(["identity", "start"]);
  });

  it("only skips the name gate when auth supplied a real profile name", () => {
    expect(hasUsableProfileName({ full_name: "Ada Lovelace" })).toBe(true);
    expect(hasUsableProfileName({ first_name: "Ada" })).toBe(true);
    expect(hasUsableProfileName({ full_name: " ", first_name: null })).toBe(false);
  });

  it("starts new setup with identity and sends old later drafts to first value", () => {
    expect(resolveOnboardingStartStep("boot", true)).toBe("identity");
    expect(resolveOnboardingStartStep("boot", false)).toBe("identity");
    expect(resolveOnboardingStartStep(undefined, false)).toBe("identity");
    expect(resolveOnboardingStartStep("connect", false)).toBe("start");
    expect(resolveOnboardingStartStep("connect", true)).toBe("identity");
    expect(resolveOnboardingStartStep("identity", false)).toBe("identity");
  });

  it("navigates from the starting outcome back to identity", () => {
    const steps = setupStepsForAudience("personal");

    expect(previousOnboardingStep(steps, "identity")).toBeNull();
    expect(previousOnboardingStep(steps, "start")).toBe("identity");
  });

  it("generates stable human-readable organization names", () => {
    const generated = generatedOrganizationName("ada@example.com");

    expect(generated).toMatch(/^[A-Z][a-z]+ [A-Z][a-z]+$/);
    expect(generatedOrganizationName("ada@example.com")).toBe(generated);
    expect(generatedOrganizationName("ada@example.com", 1)).not.toBe(generated);
  });

  it("turns the Telegram brief into agent instructions and app state", () => {
    const config = startPathLaunchConfig("telegram", {
      brief: "Capture voice notes and ask when context is missing.",
      secondaryBrief: "A searchable logbook of ideas and tasks.",
    });

    expect(config.intent).toBe("telegram_agent_companion_app");
    expect(config.message).toContain(
      "custom operating instructions: Capture voice notes",
    );
    expect(config.message).toContain(
      "companion app should keep this organized: A searchable logbook",
    );
  });

  it("frames ChatGPT as an MCP client of durable pod state", () => {
    const config = startPathLaunchConfig("chatgpt", {
      brief: "Keep the fundraising pipeline current.",
    });

    expect(config.intent).toBe("external_ai_pod_mcp");
    expect(config.message).toContain("pod-scoped Lemma MCP surface");
    expect(config.message).toContain("durable tables, files, and views");
    expect(config.message).toContain(
      "Do not pretend the external connection is complete",
    );
  });

  it("gives coding-agent users a pasteable repository-first pathway", () => {
    const prompt = codingAgentStarterPrompt("claude-code");

    expect(prompt).toContain("First inspect this repository");
    expect(prompt).toContain("Create or select one pod");
    expect(prompt).toContain("Do not create duplicates");
    expect(prompt).toContain("Claude Code session");
  });

  it("keeps a local-agent skin distinct from the coding build pathway", () => {
    const config = startPathLaunchConfig("agent-skin", {
      brief: "Plans, tasks, run status, artifacts, and review state.",
      secondaryBrief: "Approve plans and retry failed work.",
      codingAgent: "opencode",
    });

    expect(config.intent).toBe("local_agent_workspace_skin");
    expect(config.message).toContain("workspace skin around OpenCode");
    expect(config.message).toContain("Keep the local coding agent as the executor");
    expect(config.message).toContain("Approve plans and retry failed work");
  });
});
