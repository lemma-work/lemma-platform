import { describe, expect, it } from "vitest";

import {
  codingAgentStarterPrompt,
  generatedOrganizationName,
  hasUsableProfileName,
  isRetriableOrganizationNameConflict,
  nextTeamSetupStep,
  organizationNameCandidate,
  normalizeOnboardingStep,
  podNameForAudience,
  previousOnboardingStep,
  resolveOnboardingStartStep,
  setupStepsForAudience,
  startPathComposerLaunch,
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

  it("names a company organization after the company", () => {
    const candidate = (email: string, attempt = 0) =>
      organizationNameCandidate({
        email,
        workDomain: workDomainFromEmail(email),
        attempt,
      });

    expect(candidate("ada@gappy.ai")).toBe("Gappy");
    expect(candidate("ada@research.acme.co.uk")).toBe("Acme");
    expect(candidate("ada@big-corp.com")).toBe("Big Corp");
  });

  it("invents a name only when the address names no company", () => {
    for (const email of ["ada@gmail.com", "ada@icloud.com", "ada@proton.me"]) {
      expect(
        organizationNameCandidate({
          email,
          workDomain: workDomainFromEmail(email),
        }),
      ).toMatch(/^[A-Z][a-z]+ [A-Z][a-z]+$/);
    }
  });

  it("keeps a contested company recognisable instead of inventing a name", () => {
    const candidate = (attempt: number) =>
      organizationNameCandidate({
        email: "ada@acme.io",
        workDomain: "acme.io",
        attempt,
      });

    // The domain is globally unique, so one squatter on "Acme" cannot push a
    // company off a name its colleagues will still recognise.
    expect(candidate(1)).toBe("acme.io");
    expect(candidate(2)).toBe("Acme 2");
    expect(candidate(9)).toBe("Acme 9");

    // Every candidate has to be distinct, or the retry loop spins.
    const candidates = Array.from({ length: 20 }, (_, index) =>
      candidate(index),
    );
    expect(new Set(candidates).size).toBe(candidates.length);
  });

  it("retries a taken name but not a taken email domain", () => {
    expect(
      isRetriableOrganizationNameConflict({
        code: "ORGANIZATION_NAME_CONFLICT",
      }),
    ).toBe(true);
    expect(
      isRetriableOrganizationNameConflict({
        code: "ORGANIZATION_SLUG_CONFLICT",
      }),
    ).toBe(true);
    // Every candidate claims the same domain, so retrying cannot help.
    expect(
      isRetriableOrganizationNameConflict({ code: "ORGANIZATION_CONFLICT" }),
    ).toBe(false);
    expect(isRetriableOrganizationNameConflict(new Error("network"))).toBe(false);
    expect(isRetriableOrganizationNameConflict(null)).toBe(false);
  });

  it("hands the composer an unfinished sentence, not a sent message", () => {
    // The trailing space is the point: the caret lands after it, so the user is
    // completing a sentence rather than editing one.
    for (const path of [
      "telegram",
      "chatgpt",
      "internal-app",
      "agent-skin",
    ] as const) {
      const launch = startPathComposerLaunch(path);

      expect(launch.stem).toMatch(/ $/);
      expect(launch.stem.trim().length).toBeGreaterThan(0);
      expect(launch.instructions.length).toBeGreaterThan(0);
    }
  });

  it("names the pod after the path, so nothing lands as Untitled pod", () => {
    // Pressing a start path is the one thing the user has told us at that
    // point. Falling back to the placeholder name would discard it.
    const names = (
      ["telegram", "chatgpt", "internal-app", "agent-skin"] as const
    ).map((path) => startPathComposerLaunch(path).podName);

    for (const name of names) {
      expect(name.trim()).toBe(name);
      expect(name).not.toMatch(/untitled/i);
      expect(name.length).toBeGreaterThan(0);
    }
    // Distinct, so two start paths never produce two identically named pods.
    expect(new Set(names).size).toBe(names.length);
  });

  it("keeps the Telegram agent and its companion app as one build", () => {
    const launch = startPathComposerLaunch("telegram");

    expect(launch.intent).toBe("telegram_agent_companion_app");
    expect(launch.podName).toBe("Telegram Agent");
    expect(launch.stem).toBe("Build a Telegram agent and companion app that ");
    expect(launch.instructions).toContain(
      "the agent's custom operating instructions",
    );
    expect(launch.instructions).toContain(
      "Do not claim Telegram is connected until the connector is actually authorized",
    );
  });

  it("frames ChatGPT as an MCP client of durable pod state", () => {
    const launch = startPathComposerLaunch("chatgpt");

    expect(launch.intent).toBe("external_ai_pod_mcp");
    expect(launch.instructions).toContain("pod-scoped Lemma MCP surface");
    expect(launch.instructions).toContain("durable tables, files, and views");
    expect(launch.instructions).toContain(
      "Do not pretend the external connection is complete",
    );
  });

  it("puts the app in front of the agents for an internal build", () => {
    const launch = startPathComposerLaunch("internal-app");

    expect(launch.intent).toBe("internal_ai_app");
    expect(launch.podName).toBe("Internal App");
    expect(launch.stem).toBe("Build an internal app that lets my team ");
    expect(launch.instructions).toContain(
      "Put the app in front and the agents behind it",
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
    const launch = startPathComposerLaunch("agent-skin");

    expect(launch.intent).toBe("local_agent_workspace_skin");
    expect(launch.instructions).toContain(
      "Keep the local coding agent as the executor",
    );
    // The three-button picker went away with the brief screen; the agent asks
    // instead, so no path can silently build for the wrong one.
    expect(launch.instructions).toContain(
      "Codex, Claude Code, or OpenCode",
    );
  });
});
