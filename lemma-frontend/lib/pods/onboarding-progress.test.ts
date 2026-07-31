import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ONBOARDING_DRAFT_KEY,
  clearOnboardingDraft,
  findDraftBasePod,
  readOnboardingDraft,
  shouldResumeOnboarding,
  updateOnboardingDraft,
} from "./onboarding-progress";

describe("onboarding progress", () => {
  beforeEach(() => {
    vi.stubGlobal("window", {
      localStorage: new MemoryStorage(),
      dispatchEvent: vi.fn(),
    });
  });

  it("merges progress while preserving the base pod", () => {
    updateOnboardingDraft({
      step: "connect",
      ownerEmail: "person@example.com",
      audience: "personal",
      organizationId: "org-1",
      basePodId: "pod-1",
    });
    updateOnboardingDraft({ step: "start" });

    expect(readOnboardingDraft()).toMatchObject({
      step: "start",
      ownerEmail: "person@example.com",
      audience: "personal",
      organizationId: "org-1",
      basePodId: "pod-1",
    });
  });

  it("rejects invalid or unknown draft versions", () => {
    window.localStorage.setItem(ONBOARDING_DRAFT_KEY, "not-json");
    expect(readOnboardingDraft()).toBeNull();

    window.localStorage.setItem(
      ONBOARDING_DRAFT_KEY,
      JSON.stringify({ version: 2, step: "connect" }),
    );
    expect(readOnboardingDraft()).toBeNull();
  });

  it("clears completed progress", () => {
    updateOnboardingDraft({ step: "start", basePodId: "pod-1" });
    clearOnboardingDraft();
    expect(readOnboardingDraft()).toBeNull();
  });

  it("keeps onboarding active after the early pod has been created", () => {
    const draft = updateOnboardingDraft({
      step: "connect",
      basePodId: "pod-1",
    });

    expect(shouldResumeOnboarding(draft, 1)).toBe(true);
    expect(shouldResumeOnboarding(null, 1)).toBe(false);
  });

  it("rehydrates and reuses the draft pod", () => {
    const draft = updateOnboardingDraft({ basePodId: "pod-1" });
    const pods = [{ id: "pod-1" }, { id: "pod-2" }];

    expect(findDraftBasePod(null, pods, draft)).toBe(pods[0]);
    expect(findDraftBasePod(pods[1], pods, draft)).toBe(pods[1]);
  });

  it("recovers the only organization pod if persistence was interrupted", () => {
    const draft = updateOnboardingDraft({
      organizationId: "org-1",
      basePodId: null,
    });
    const pod = { id: "pod-1", organization_id: "org-1" };

    expect(findDraftBasePod(null, [pod], draft)).toBe(pod);
  });
});

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length() {
    return this.values.size;
  }

  clear() {
    this.values.clear();
  }

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string) {
    this.values.delete(key);
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}
