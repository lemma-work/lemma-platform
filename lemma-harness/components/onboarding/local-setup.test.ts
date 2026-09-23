import { describe, expect, it } from "vitest";

import {
  LOCAL_SETUP_STEPS,
  normalizeOnboardingStep,
  setupStepsForAudience,
  type SetupStep,
} from "./account-onboarding-helpers";

describe("local onboarding", () => {
  it("asks every question only a local installation can answer", () => {
    // Hosted Lemma has models of its own; a local install has none until
    // someone points it at a provider, so the provider step is what decides
    // whether agents work at all. Losing it is the bug this flow exists to fix.
    expect(LOCAL_SETUP_STEPS).toContain("intelligence");
    expect(LOCAL_SETUP_STEPS).toContain("sharing");
  });

  it("asks nothing a hosted signup is not asked", () => {
    // A hosted account is given a name and a first pod without being asked for
    // either. A local one knows exactly as much about its user, so asking was
    // the difference — and it made the deployment that provisions nothing
    // automatically also the one that made the user do it by hand.
    expect(LOCAL_SETUP_STEPS).not.toContain("identity");
    expect(LOCAL_SETUP_STEPS).not.toContain("start");
    expect(LOCAL_SETUP_STEPS).not.toContain("workspace");
  });

  it("asks what answers in chats before anything can depend on it", () => {
    // Sharing is what turns a provider into a shared default, so it has to come
    // after the user has seen which one they picked — and the pod is created on
    // the far side of it, with that provider already chosen.
    expect(LOCAL_SETUP_STEPS.indexOf("intelligence")).toBeLessThan(
      LOCAL_SETUP_STEPS.indexOf("sharing"),
    );
    expect(LOCAL_SETUP_STEPS.at(-1)).toBe("sharing");
  });

  it("keeps the model and the local agents on one screen", () => {
    // They are one decision — what answers in chats — and splitting them meant
    // two screens plus a second window to finish the first of them.
    expect(LOCAL_SETUP_STEPS).not.toContain("provider");
    expect(LOCAL_SETUP_STEPS).not.toContain("agents");
  });

  it("uses the local list regardless of audience", () => {
    // Solo users skip the hosted `connect` step entirely, which is how a local
    // user could reach a workspace having never been asked about a model.
    expect(setupStepsForAudience("personal")).not.toContain("intelligence");
    expect(setupStepsForAudience("personal", true)).toEqual(LOCAL_SETUP_STEPS);
    expect(setupStepsForAudience("team", true)).toEqual(LOCAL_SETUP_STEPS);
  });

  it("recovers a draft that names a step this flow does not have", () => {
    // A draft written against hosted Lemma by the same account can name
    // `workspace` or `team`, and one written by an older local build names
    // `identity` or `start`. Resuming on any of them would strand the user on a
    // screen this flow never renders.
    expect(normalizeOnboardingStep("workspace", "team", true, true)).toBe(
      "intelligence",
    );
    expect(normalizeOnboardingStep("connect", "personal", false, true)).toBe(
      "intelligence",
    );
    expect(normalizeOnboardingStep("identity", "personal", true, true)).toBe(
      "intelligence",
    );
    expect(normalizeOnboardingStep("start", "personal", true, true)).toBe(
      "intelligence",
    );
    for (const step of LOCAL_SETUP_STEPS) {
      expect(normalizeOnboardingStep(step, "personal", true, true)).toBe(step);
    }
  });

  it("leaves hosted normalization untouched", () => {
    const hosted: SetupStep = "team";
    expect(normalizeOnboardingStep(hosted, "team", false)).toBe("workspace");
    expect(normalizeOnboardingStep(hosted, "team", true)).toBe("team");
  });
});
