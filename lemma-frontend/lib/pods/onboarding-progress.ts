import type {
  Audience,
  SetupStep,
  TeamKind,
} from "@/components/onboarding/account-onboarding-helpers";

export const ONBOARDING_DRAFT_KEY = "lemma:onboarding-draft:v1";
const ONBOARDING_DRAFT_EVENT = "lemma:onboarding-draft-change";
let cachedRaw: string | null | undefined;
let cachedDraft: OnboardingDraft | null = null;

export type OnboardingDraft = {
  version: 1;
  ownerEmail: string | null;
  step: SetupStep;
  audience: Audience | null;
  teamKind: TeamKind | null;
  customTeamName: string;
  workspaceName: string;
  allowDomainJoin: boolean;
  organizationId: string | null;
  basePodId: string | null;
};

export function shouldResumeOnboarding(
  draft: OnboardingDraft | null,
  accessiblePodCount: number,
): boolean {
  return Boolean(draft) || accessiblePodCount === 0;
}

export function findDraftBasePod<T extends { id: string }>(
  current: T | null,
  accessiblePods: T[],
  draft: OnboardingDraft | null,
): T | null {
  if (current) return current;
  const exact = accessiblePods.find((pod) => pod.id === draft?.basePodId);
  if (exact) return exact;

  if (draft && !draft.basePodId && draft.organizationId) {
    const organizationPods = accessiblePods.filter(
      (pod) =>
        "organization_id" in pod &&
        pod.organization_id === draft.organizationId,
    );
    if (organizationPods.length === 1) return organizationPods[0];
  }

  return null;
}

const SETUP_STEPS = new Set<SetupStep>([
  "boot",
  "identity",
  "audience",
  "team",
  "workspace",
  "connect",
  "start",
]);

const AUDIENCES = new Set<Audience>(["personal", "team"]);
const TEAM_KINDS = new Set<TeamKind>([
  "sales",
  "support",
  "operations",
  "recruiting",
  "customer-success",
  "product",
  "finance",
  "other",
]);

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function parseOnboardingDraft(raw: string | null): OnboardingDraft | null {
  if (!raw) return null;

  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (value.version !== 1 || !SETUP_STEPS.has(value.step as SetupStep)) {
      return null;
    }

    const audience = AUDIENCES.has(value.audience as Audience)
      ? (value.audience as Audience)
      : null;
    const teamKind = TEAM_KINDS.has(value.teamKind as TeamKind)
      ? (value.teamKind as TeamKind)
      : null;

    return {
      version: 1,
      ownerEmail: optionalString(value.ownerEmail)?.toLowerCase() || null,
      step: value.step as SetupStep,
      audience,
      teamKind,
      customTeamName:
        typeof value.customTeamName === "string" ? value.customTeamName : "",
      workspaceName:
        typeof value.workspaceName === "string" ? value.workspaceName : "",
      allowDomainJoin: value.allowDomainJoin === true,
      organizationId: optionalString(value.organizationId),
      basePodId: optionalString(value.basePodId),
    };
  } catch {
    return null;
  }
}

function notifyDraftChanged(): void {
  try {
    window.dispatchEvent(new Event(ONBOARDING_DRAFT_EVENT));
  } catch {
    // Some restricted webviews do not expose Event constructors.
  }
}

export function readOnboardingDraft(): OnboardingDraft | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(ONBOARDING_DRAFT_KEY);
    if (raw === cachedRaw) return cachedDraft;
    cachedRaw = raw;
    cachedDraft = parseOnboardingDraft(raw);
    return cachedDraft;
  } catch {
    return null;
  }
}

export function updateOnboardingDraft(
  patch: Partial<Omit<OnboardingDraft, "version">>,
): OnboardingDraft | null {
  if (typeof window === "undefined") return null;

  const current = readOnboardingDraft();
  const next: OnboardingDraft = {
    version: 1,
    ownerEmail: null,
    step: "audience",
    audience: null,
    teamKind: null,
    customTeamName: "",
    workspaceName: "",
    allowDomainJoin: false,
    organizationId: null,
    basePodId: null,
    ...current,
    ...patch,
  };

  try {
    const raw = JSON.stringify(next);
    window.localStorage.setItem(ONBOARDING_DRAFT_KEY, raw);
    cachedRaw = raw;
    cachedDraft = next;
    notifyDraftChanged();
    return next;
  } catch {
    return null;
  }
}

export function clearOnboardingDraft(): void {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.removeItem(ONBOARDING_DRAFT_KEY);
    cachedRaw = null;
    cachedDraft = null;
    notifyDraftChanged();
  } catch {
    // localStorage can be unavailable in private or restricted contexts.
  }
}

export function subscribeToOnboardingDraft(callback: () => void) {
  if (typeof window === "undefined") return () => undefined;

  const handleStorage = (event: StorageEvent) => {
    if (event.key === ONBOARDING_DRAFT_KEY) callback();
  };
  window.addEventListener("storage", handleStorage);
  window.addEventListener(ONBOARDING_DRAFT_EVENT, callback);
  return () => {
    window.removeEventListener("storage", handleStorage);
    window.removeEventListener(ONBOARDING_DRAFT_EVENT, callback);
  };
}
