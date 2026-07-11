import { describe, expect, it } from "vitest";

import { setupStepsForAudience } from "@/components/onboarding/account-onboarding-helpers";

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
});
