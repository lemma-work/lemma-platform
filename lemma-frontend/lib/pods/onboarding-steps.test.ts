import { describe, expect, it } from "vitest";

import {
  previousOnboardingStep,
  resolveOnboardingStartStep,
  setupStepsForAudience,
} from "@/components/onboarding/account-onboarding-helpers";

describe("onboarding step paths", () => {
  it("routes team onboarding through workspace selection", () => {
    expect(setupStepsForAudience("team")).toEqual([
      "boot",
      "identity",
      "audience",
      "team",
      "workspace",
      "connect",
      "start",
    ]);
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
