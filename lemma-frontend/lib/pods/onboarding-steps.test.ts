import { describe, expect, it } from "vitest";

import {
  defaultWorkspaceName,
  nextTeamSetupStep,
  normalizeOnboardingStep,
  podNameForAudience,
  previousOnboardingStep,
  resolveOnboardingStartStep,
  setupStepsForAudience,
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

  it("keeps personal onboarding workspace-free", () => {
    expect(setupStepsForAudience("personal")).not.toContain("workspace");
  });

  it("does not resume a persisted boot step", () => {
    expect(resolveOnboardingStartStep("boot", true)).toBe("identity");
    expect(resolveOnboardingStartStep("boot", false)).toBe("audience");
    expect(resolveOnboardingStartStep("connect", true)).toBe("connect");
  });

  it("does not navigate back into the non-resumable boot step", () => {
    const steps = setupStepsForAudience("personal");

    expect(previousOnboardingStep(steps, "identity")).toBeNull();
    expect(previousOnboardingStep(steps, "audience")).toBe("identity");
  });
});
