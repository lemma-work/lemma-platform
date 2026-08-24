"use client";

import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { WaitingScreen } from "@/components/shared/loading";
import { useOrganization } from "@/components/dashboard/org-context";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import {
  readLastOpenedPodId,
  subscribeToLastOpenedPodId,
} from "@/lib/pods/last-opened-pod";
import {
  hasSkippedFirstPod as skipBelongsToOwner,
  readOnboardingSkippedFirstPod,
  subscribeToOnboardingSkippedFirstPod,
} from "@/lib/pods/onboarding-skip";
import { buildComposerLaunchHref } from "@/lib/pods/composer-launch";
import {
  trackOnboardingStep,
  trackPodReady,
} from "@/lib/analytics/onboarding";
import {
  clearOnboardingDraft,
  findDraftBasePod,
  readOnboardingDraft,
  shouldResumeOnboarding,
  subscribeToOnboardingDraft,
  updateOnboardingDraft,
  type OnboardingDraft,
} from "@/lib/pods/onboarding-progress";
import {
  useCreateOrganization,
  useJoinSuggestedOrganization,
  useMyOrganizationInvitations,
  useSuggestedOrganizations,
} from "@/lib/hooks/use-organizations";
import { useAccessiblePods, type AccessiblePod } from "@/lib/hooks/use-pods";
import { useProfile, useUpdateProfile } from "@/lib/hooks/use-user";
import {
  useCreateAgentRuntime,
  useUpdatePodDefaultAgentRuntime,
} from "@/lib/hooks/use-agent-runtime";
import { adoptableLocalAgent } from "@/lib/desktop/local-agent-default";
import {
  OrganizationInvitationStatus,
  OrganizationJoinPolicy,
  type Organization,
  type Pod,
} from "@/lib/types";
import {
  normalizeEmailDomain,
  workDomainFromEmail,
} from "@/lib/utils/organization-slugs";
import {
  FIRST_RUN_DELIGHT,
} from "@/lib/recipes/recipes";

import { SetupChrome, SetupShell } from "./account-onboarding-chrome";
import {
  hasUsableProfileName,
  inferFullName,
  nextTeamSetupStep,
  normalizeOnboardingStep,
  organizationNameCandidate,
  podNameForAudience,
  setupStepsForAudience,
  splitName,
  startPathComposerLaunch,
  teamLabelForKind,
  previousOnboardingStep,
  resolveOnboardingStartStep,
  type Audience,
  type ConnectChoice,
  type OnboardingStartPath,
  type SetupStep,
  type TeamKind,
} from "./account-onboarding-helpers";
import {
  AudienceStep,
  BootStep,
  ConnectStep,
  IdentityStep,
  InvitationsStep,
  StartStep,
  TeamStep,
  WorkspaceStep,
} from "./account-onboarding-steps";
import { LocalIntelligenceStep, LocalSharingStep } from "./local-setup-steps";
import { isLocalDeployment } from "@/lib/config";
import { useFirstPodProvisioning } from "./use-first-pod-provisioning";
import { readLocalAiStatus } from "@/lib/desktop/local-capabilities";

/** What onboarding needs of a pod, whether it created it or restored it. */
type OnboardingBasePod = { id: string; name: string; organization_id: string };

const AnomalousOrb = dynamic(
  () => import("@/components/ui/anomalous-orb").then((module) => module.AnomalousOrb),
  { ssr: false },
);

export function AccountOnboarding({
  children,
  requireFirstPod = true,
  preflightFallback,
}: {
  children: React.ReactNode;
  requireFirstPod?: boolean;
  preflightFallback?: React.ReactNode;
}) {
  const { data: profile, isLoading: isLoadingProfile } = useProfile();
  const {
    currentOrg,
    organizations,
    isLoading: isLoadingOrganizations,
    setCurrentOrg,
  } = useOrganization();
  const isProfileComplete = hasUsableProfileName(profile);
  const lastOpenedPodId = useSyncExternalStore(
    subscribeToLastOpenedPodId,
    readLastOpenedPodId,
    () => null,
  );
  const hasLastOpenedPod = requireFirstPod && Boolean(lastOpenedPodId);
  const skippedFirstPodOwner = useSyncExternalStore(
    subscribeToOnboardingSkippedFirstPod,
    readOnboardingSkippedFirstPod,
    () => null,
  );
  // Only this account's own skip counts. An unowned flag left by whoever used
  // this browser last used to switch first-pod provisioning off for everyone
  // after them, which is exactly how a new account landed on an empty home.
  const hasSkippedFirstPod =
    requireFirstPod && skipBelongsToOwner(skippedFirstPodOwner, profile?.email);
  const storedOnboardingDraft = useSyncExternalStore(
    subscribeToOnboardingDraft,
    readOnboardingDraft,
    () => null,
  );
  const onboardingDraft =
    storedOnboardingDraft?.ownerEmail === profile?.email?.trim().toLowerCase()
      ? storedOnboardingDraft
      : null;
  const hasOnboardingDraft = requireFirstPod && Boolean(onboardingDraft);
  const { data: podsData, isLoading: isLoadingPods } = useAccessiblePods({
    enabled:
      requireFirstPod &&
      (hasOnboardingDraft || (!hasLastOpenedPod && !hasSkippedFirstPod)),
  });
  const pods = podsData?.items || [];
  const { data: invitationsData, isLoading: isLoadingInvitations } =
    useMyOrganizationInvitations(OrganizationInvitationStatus.PENDING, {
      // Prefetch while the user supplies a missing name so an invite can take
      // over immediately afterward instead of racing the direct-start screen.
      enabled: Boolean(profile?.email),
    });
  const pendingInvitations = invitationsData?.items || [];
  const needsProfile = Boolean(profile) && !isProfileComplete;
  const needsOrganization =
    !isLoadingOrganizations && organizations.length === 0;
  const needsFirstPod =
    requireFirstPod &&
    (hasOnboardingDraft || (!hasLastOpenedPod && !hasSkippedFirstPod)) &&
    !isLoadingPods &&
    pendingInvitations.length === 0 &&
    shouldResumeOnboarding(onboardingDraft, pods.length);
  // A pending invitation wins outright. It used to lose to the identity step,
  // which put a name question and a screen in front of the one arrival that was
  // already perfect: the invite carries a destination, so an invited teammate
  // can go straight to the pod -- and to the app -- they were invited to.
  const needsInvitations = pendingInvitations.length > 0;
  const needsIdentityStep =
    !needsInvitations &&
    (needsProfile || (!onboardingDraft && (needsOrganization || needsFirstPod)));
  const [setupActive, setSetupActive] = useState(false);

  // Hosted accounts with nothing yet are provisioned rather than interviewed.
  // A local installation is excluded: it still owes the user a model before a
  // pod means anything, which is what its own steps are for. A draft means a
  // half-finished run of the old flow, which stays with the old flow.
  const isLocal = isLocalDeployment();
  const canAutoProvision =
    !isLocal &&
    !setupActive &&
    !needsInvitations &&
    !onboardingDraft &&
    !isLoadingProfile &&
    !isLoadingOrganizations &&
    !isLoadingInvitations &&
    Boolean(profile) &&
    (needsOrganization || needsFirstPod);
  const autoSuggestedOrganizations = useSuggestedOrganizations({
    enabled: canAutoProvision && organizations.length === 0,
  });
  // Resolved before provisioning starts, because creating an organization for
  // someone whose colleague already claimed the domain is the one mistake here
  // that a rename cannot undo.
  const suggestionsSettled =
    organizations.length > 0 || !autoSuggestedOrganizations.isLoading;
  const provisioning = useFirstPodProvisioning({
    enabled: canAutoProvision && suggestionsSettled,
    profile,
    organizations,
    suggestedOrganization: autoSuggestedOrganizations.data?.items?.[0] ?? null,
  });
  const nextSetupStep = resolveOnboardingStartStep(
    onboardingDraft?.step,
    needsIdentityStep,
  );
  const setupInitialStep: SetupStep =
    nextSetupStep;

  if (
    !setupActive &&
    (isLoadingProfile ||
      isLoadingOrganizations ||
      (requireFirstPod &&
        (hasOnboardingDraft || !hasLastOpenedPod) &&
        isLoadingPods) ||
      isLoadingInvitations)
  ) {
    if (preflightFallback) {
      return preflightFallback;
    }

    return (
      <SetupShell>
        <WaitingScreen
          title="Preparing your workspace"
          description="Checking identity, workspace, invitations, and pods."
          className="w-full max-w-xl"
        />
      </SetupShell>
    );
  }

  // `navigated` keeps this held after `canAutoProvision` goes false: the pod now
  // exists, so the conditions that justified provisioning have stopped being
  // true, and falling through here renders the redirect that overwrites the
  // composer launch with a bare pod URL.
  if ((canAutoProvision || provisioning === "navigated") && provisioning !== "failed") {
    return preflightFallback ?? (
      <SetupShell>
        <WaitingScreen
          title="Setting up your workspace"
          description="This takes a moment. You will land straight in your pod."
          className="w-full max-w-xl"
        />
      </SetupShell>
    );
  }

  if (needsInvitations) {
    return (
      <InvitationsStep
        invitations={pendingInvitations}
        signupAt={profile?.created_at ?? null}
        ownerEmail={profile?.email ?? null}
      />
    );
  }

  if (needsProfile || needsOrganization || needsFirstPod || setupActive) {
    return (
      <SetupAssistant
        profile={profile}
        organizations={organizations}
        accessiblePods={pods}
        initialDraft={onboardingDraft}
        initialOrganization={
          organizations.find(
            (organization) => organization.id === onboardingDraft?.organizationId,
          ) ||
          currentOrg ||
          organizations[0] ||
          null
        }
        initialAudience="personal"
        deferWorkspaceForInvitation={pendingInvitations.length > 0}
        startStep={nextSetupStep}
        initialStep={setupInitialStep}
        onSetupStart={() => setSetupActive(true)}
        onOrganizationReady={setCurrentOrg}
      />
    );
  }

  return <>{children}</>;
}

function SetupAssistant({
  profile,
  organizations,
  accessiblePods,
  initialDraft,
  initialOrganization,
  initialAudience,
  deferWorkspaceForInvitation,
  startStep,
  initialStep,
  onSetupStart,
  onOrganizationReady,
}: {
  profile?: {
    email?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    full_name?: string | null;
    created_at?: string | null;
  } | null;
  organizations: Organization[];
  // The navigation listing, not a full pod record: this only ever reads `id`
  // and `organization_id` to restore a draft's base pod.
  accessiblePods: AccessiblePod[];
  initialDraft: OnboardingDraft | null;
  initialOrganization: Organization | null;
  initialAudience: Audience | null;
  deferWorkspaceForInvitation: boolean;
  startStep: SetupStep;
  initialStep: SetupStep;
  onSetupStart: () => void;
  onOrganizationReady: (organization: Organization) => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const updateProfile = useUpdateProfile();
  const createOrganization = useCreateOrganization();
  const joinSuggestedOrganization = useJoinSuggestedOrganization();
  const createAgentRuntime = useCreateAgentRuntime();
  const updatePodDefaultRuntime = useUpdatePodDefaultAgentRuntime();
  const email = profile?.email || "";
  const saveOnboardingDraft = (
    patch: Parameters<typeof updateOnboardingDraft>[0],
  ) =>
    updateOnboardingDraft({
      ownerEmail: email.trim().toLowerCase() || null,
      ...patch,
    });
  const workDomain = workDomainFromEmail(email);
  const normalizedWorkDomain = normalizeEmailDomain(workDomain);
  const inferredName = inferFullName(profile);
  const initialIdentityName = hasUsableProfileName(profile) ? inferredName : "";
  // A local installation runs its own step list. Resolved once here rather than
  // per render: the deployment cannot change while the app is open, and a step
  // list that flickered would strand the user mid-flow.
  const isLocal = isLocalDeployment();
  const normalizedInitialStep = normalizeOnboardingStep(
    initialStep,
    initialAudience,
    Boolean(initialOrganization),
    isLocal,
  );
  const [step, setStep] = useState<SetupStep>(normalizedInitialStep);
  const signupAt = profile?.created_at ?? null;
  // Every step renders at "/", so this is the only thing that can tell the
  // funnel's stages apart -- a pageview cannot.
  useEffect(() => {
    trackOnboardingStep(step);
  }, [step]);
  const [createdOrganization, setCreatedOrganization] =
    useState<Organization | null>(null);
  // Either a pod just created here (a full record) or one restored from the
  // navigation listing, and onboarding only ever reads its id and name — so the
  // state says that rather than claiming a full pod it does not always hold.
  const [basePod, setBasePod] = useState<OnboardingBasePod | null>(() =>
    findDraftBasePod<OnboardingBasePod>(null, accessiblePods, initialDraft),
  );
  const identitySubmissionRef = useRef(false);
  const createPodPromiseRef = useRef<Promise<OnboardingBasePod | null> | null>(null);
  const [isCreatingPod, setIsCreatingPod] = useState(false);
  const [isConnectingAi, setIsConnectingAi] = useState(false);
  const [connectedProfileId, setConnectedProfileId] = useState<string | null>(
    null,
  );
  const [identityName, setIdentityName] = useState(initialIdentityName);
  const [identityCompletedLocally, setIdentityCompletedLocally] = useState(false);
  const [workspaceName, setWorkspaceName] = useState(
    initialDraft?.workspaceName ||
      organizationNameCandidate({ email, workDomain: normalizedWorkDomain }),
  );
  const [audience, setAudience] = useState<Audience | null>(
    "personal",
  );
  const [teamKind, setTeamKind] = useState<TeamKind | null>(
    initialDraft?.teamKind || "support",
  );
  const [customTeamName, setCustomTeamName] = useState(
    initialDraft?.customTeamName || "",
  );
  const [allowDomainJoin, setAllowDomainJoin] = useState(
    initialDraft?.allowDomainJoin ?? Boolean(normalizedWorkDomain),
  );
  const suggestedOrganizations = useSuggestedOrganizations({
    enabled:
      Boolean(profile?.email) &&
      organizations.length === 0,
  });
  const suggestedOrganization = suggestedOrganizations.data?.items?.[0] || null;
  const activeOrganization = createdOrganization || initialOrganization;

  useEffect(() => {
    if (
      normalizedInitialStep === "identity" &&
      !identityCompletedLocally &&
      step !== "identity"
    ) {
      setStep("identity");
      return;
    }

    if (step === "boot" && normalizedInitialStep !== step) {
      setStep(normalizedInitialStep);
    }
  }, [identityCompletedLocally, normalizedInitialStep, step]);

  useEffect(() => {
    if (!basePod && initialDraft?.basePodId) {
      const restored = accessiblePods.find(
        (pod) => pod.id === initialDraft.basePodId,
      );
      if (restored) setBasePod(restored);
    }
  }, [accessiblePods, basePod, initialDraft?.basePodId]);

  useEffect(() => {
    if (!initialDraft) setAllowDomainJoin(Boolean(normalizedWorkDomain));
  }, [initialDraft, normalizedWorkDomain]);

  const goTo = (nextStep: SetupStep) => {
    onSetupStart();
    saveOnboardingDraft({ step: nextStep });
    setStep(nextStep);
  };

  const handleBegin = () => {
    goTo(startStep);
  };

  /**
   * Create this person's first organization, stepping through name candidates
   * when the server says one is taken.
   *
   * The probe on the identity step cannot be what guarantees uniqueness: two
   * colleagues signing up in the same minute both pass it and one still loses
   * the race. So the ladder is walked here too, against the only authority
   * there is, and setup survives a collision instead of dead-ending on it.
   */
  /**
   * The workspace nobody asked for, created without asking.
   *
   * This used to walk a twenty-rung name ladder from the browser -- one
   * sequential create per rung, on the critical path of a signup, for a name
   * the user never typed. The server resolves its own collision now, so this is
   * one call that cannot fail on a name.
   */
  const createFirstOrganization = async (): Promise<Organization> => {
    const useDomainJoin = Boolean(normalizedWorkDomain);

    return createOrganization.mutateAsync({
      name: organizationNameCandidate({
        email,
        workDomain: normalizedWorkDomain,
      }),
      join_policy: useDomainJoin
        ? OrganizationJoinPolicy.EMAIL_DOMAIN
        : OrganizationJoinPolicy.INVITE_ONLY,
      email_domain: useDomainJoin ? normalizedWorkDomain : null,
      resolve_name_conflicts: true,
    });
  };

  const handleIdentitySubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (identitySubmissionRef.current) return;

    const parsed = splitName(identityName);
    if (!parsed.firstName) return;

    identitySubmissionRef.current = true;
    try {
      await updateProfile.mutateAsync({
        first_name: parsed.firstName,
        last_name: parsed.lastName || null,
      });

      setAudience("personal");
      setIdentityCompletedLocally(true);

      // A direct invitation remains the strongest destination. Persist the
      // completed first step so AccountOnboarding can accept it immediately.
      if (deferWorkspaceForInvitation) {
        saveOnboardingDraft({
          step: "start",
          audience: "personal",
          workspaceName,
        });
        return;
      }

      let organization = activeOrganization;
      if (!organization && suggestedOrganization) {
        organization = await joinSuggestedOrganization.mutateAsync(
          suggestedOrganization.id,
        );
        toast.success(`Joined ${organization.name}`);
      } else if (!organization) {
        organization = await createFirstOrganization();
        toast.success(`${organization.name} created`);
      }

      setCreatedOrganization(organization);
      onOrganizationReady(organization);
      // The name the server settled on, which a collision may have moved off
      // the candidate we were showing.
      setWorkspaceName(organization.name);
      saveOnboardingDraft({
        audience: "personal",
        workspaceName: organization.name,
        organizationId: organization.id,
      });

      if (suggestedOrganization) {
        const existingPods = await getLemmaClient().pods.listByOrganization(
          organization.id,
        );
        if (existingPods.items.length > 0) {
          clearOnboardingDraft();
          router.replace("/home");
          return;
        }
      }

      // Local installs owe the user three more answers before they can be
      // handed a working workspace — most importantly a model, without which
      // agents silently do nothing.
      goTo(isLocal ? "intelligence" : "start");
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Could not finish setup";
      toast.error(message);
    } finally {
      identitySubmissionRef.current = false;
    }
  };

  /**
   * Leaving the intelligence step, whether or not anything was configured.
   *
   * Deferring is a real choice — the rest of Lemma works without AI, and the
   * banner in the workspace keeps asking — so it advances just the same. What
   * it must not do is claim the step succeeded.
   */
  const handleLocalIntelligenceContinue = (outcome: "ready" | "deferred") => {
    if (outcome === "deferred") {
      toast.info("Agents stay unavailable until a model or a coding agent is set.");
    }
    goTo("sharing");
  };

  const resolveTeamName = (kind = teamKind, customName = customTeamName) =>
    teamLabelForKind(kind, customName);

  const ensureOrganization = async (
    audienceForPod: Audience,
    organizationOverride?: Organization | null,
  ): Promise<Organization | null> => {
    if (organizationOverride) return organizationOverride;
    if (activeOrganization) return activeOrganization;

    // Team workspaces are user-owned identity and must be created or joined
    // explicitly before a team pod can be created.
    if (audienceForPod === "team") return null;

    // Same ladder as the identity step. Two silent paths inventing two
    // different names for the same person is how "Ada's Space" ended up beside
    // "Cedar Harbor" — and the second Ada on the platform hit a hard conflict.
    const organization = await createFirstOrganization();
    setCreatedOrganization(organization);
    onOrganizationReady(organization);
    saveOnboardingDraft({ organizationId: organization.id });
    return organization;
  };

  const createBasePod = async (
    audienceForPod: Audience,
    teamName = "",
    organizationOverride?: Organization | null,
    // What the chosen start path calls this pod. Picking "Build a Telegram
    // agent" and landing in one called "Personal Pod" throws away the only
    // thing the user has said so far.
    nameOverride?: string,
  ): Promise<OnboardingBasePod | null> => {
    const restoredCandidate = findDraftBasePod(
      basePod,
      accessiblePods,
      initialDraft,
    );
    const intendedOrganizationId =
      organizationOverride?.id ||
      activeOrganization?.id ||
      initialDraft?.organizationId ||
      null;
    const restoredPod =
      restoredCandidate &&
      (!intendedOrganizationId ||
        restoredCandidate.organization_id === intendedOrganizationId)
        ? restoredCandidate
        : null;
    if (restoredPod) {
      setBasePod(restoredPod);
      return restoredPod;
    }
    if (createPodPromiseRef.current) return createPodPromiseRef.current;

    setIsCreatingPod(true);
    const creation = (async () => {
      try {
        const organization = await ensureOrganization(
          audienceForPod,
          organizationOverride,
        );
        if (!organization) {
          toast.error("Could not prepare your workspace");
          return null;
        }

        const podName =
          nameOverride || podNameForAudience(audienceForPod, teamName);
        const pod = await getLemmaClient().pods.create({
          name: podName,
          description:
            audienceForPod === "personal"
              ? "A private workspace for apps, surface agents, knowledge, and operating loops."
              : `${teamName || "Team"}'s shared workspace for apps, surface agents, knowledge, and operating loops.`,
          organization_id: organization.id,
        });
        setBasePod(pod);
        // A coding agent picked during setup has to actually answer in this
        // pod. Nothing else was doing that: the pod's default runtime stayed
        // pointed at the installation provider, which on a local install is
        // usually unconfigured — so a user who deliberately chose Claude Code
        // got "check the agent runtime configuration" on their first message.
        await adoptLocalAgentAsPodDefault(pod);
        saveOnboardingDraft({
          organizationId: organization.id,
          basePodId: pod.id,
        });
        queryClient.invalidateQueries({ queryKey: ["pods"] });
        toast.success(`${pod.name} created`);
        return pod;
      } catch (error) {
        const message =
          error instanceof Error && error.message
            ? error.message
            : "Failed to create pod";
        toast.error(message);
        return null;
      } finally {
        setIsCreatingPod(false);
        createPodPromiseRef.current = null;
      }
    })();
    createPodPromiseRef.current = creation;
    return creation;
  };

  /**
   * Give a new pod a runtime that actually answers, before anyone opens it.
   *
   * The pod experience needs a default. Without one it falls back to the
   * installation provider, which on a local install is usually unconfigured —
   * so the first message dies on "check the agent runtime configuration" and
   * the pod is useless from the moment it is created.
   *
   * So this is not best-effort. When there is no working provider and the user
   * did pick an agent, that agent becomes the default or they are told the pod
   * has no model — never left to discover it by sending a message.
   */
  const adoptLocalAgentAsPodDefault = async (pod: Pod) => {
    if (!isLocal) return;
    // A configured provider already answers as the system default; the pod
    // needs nothing of its own.
    if ((await readLocalAiStatus()) === "ready") return;

    // Read the agents now, from the server, rather than from a cached listing.
    // The agent this is here to adopt was created seconds ago, in the step
    // before this one, so any cache old enough to still be warm is old enough
    // to predate it — which is exactly how this silently adopted nothing and
    // left pods failing on "No LLM model is configured on this server".
    let agent: { id: string; name: string } | null = null;
    try {
      const profiles = await getLemmaClient().agentRuntime.listProfiles(
        pod.organization_id,
      );
      agent = adoptableLocalAgent(profiles.items);
    } catch {
      // Unreachable server: the pod exists and setup continues. The workspace
      // banner covers a pod with no model, and the composer's picker is the
      // way out of it.
      return;
    }
    if (!agent) {
      // Nothing was configured at all — the deferred path. The workspace
      // banner is already saying so, so this stays quiet rather than
      // repeating it at pod creation.
      return;
    }

    try {
      await updatePodDefaultRuntime.mutateAsync({
        podId: pod.id,
        runtime: { profile_id: agent.id },
      });
    } catch (error) {
      // Loud, because the pod does not work without this.
      toast.error(
        `${agent.name} could not be set as this pod's model: `
          + `${error instanceof Error ? error.message : "unknown error"}. `
          + "Pick a model in the composer before sending a message.",
      );
    }
  };

  const handleAudienceSelect = async (value: Audience) => {
    setAudience(value);
    saveOnboardingDraft({ audience: value });

    if (value === "team") {
      goTo(
        nextTeamSetupStep({
          hasOrganization: Boolean(activeOrganization),
          hasPod: false,
        }),
      );
      return;
    }

    const pod = await createBasePod("personal");
    if (pod) goTo("connect");
  };

  const handleTeamContinue = async () => {
    const teamName = resolveTeamName();
    if (!teamName.trim()) {
      toast.error("Choose or name a team first");
      return;
    }

    saveOnboardingDraft({
      audience: "team",
      teamKind,
      customTeamName,
    });

    if (!activeOrganization) {
      goTo("workspace");
      return;
    }

    const pod = await createBasePod("team", teamName, activeOrganization);
    if (pod) {
      goTo(
        nextTeamSetupStep({
          hasOrganization: true,
          hasPod: true,
        }),
      );
    }
  };

  const handleJoinSuggested = async () => {
    if (!suggestedOrganization) return;

    try {
      const organization = await joinSuggestedOrganization.mutateAsync(
        suggestedOrganization.id,
      );
      toast.success(`Joined ${organization.name}`);
      setCreatedOrganization(organization);
      onOrganizationReady(organization);
      saveOnboardingDraft({ organizationId: organization.id });

      const existingPods = await getLemmaClient().pods.listByOrganization(
        organization.id,
      );
      if (existingPods.items.length > 0) {
        clearOnboardingDraft();
        router.replace("/home");
        return;
      }

      goTo(
        nextTeamSetupStep({
          hasOrganization: true,
          hasPod: false,
        }),
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      toast.error(`Could not join workspace: ${message}`);
    }
  };

  const handleCreateWorkspace = async () => {
    const useDomainJoin = allowDomainJoin && Boolean(normalizedWorkDomain);
    try {
      const organization = await createOrganization.mutateAsync({
        name: workspaceName.trim(),
        join_policy: useDomainJoin
          ? OrganizationJoinPolicy.EMAIL_DOMAIN
          : OrganizationJoinPolicy.INVITE_ONLY,
        email_domain: useDomainJoin ? normalizedWorkDomain : null,
      });
      toast.success(`${organization.name} created`);
      setCreatedOrganization(organization);
      onOrganizationReady(organization);
      saveOnboardingDraft({ organizationId: organization.id });
      goTo(
        nextTeamSetupStep({
          hasOrganization: true,
          hasPod: false,
        }),
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      toast.error(`Failed to create workspace: ${message}`);
    }
  };

  const handleConnectContinue = async (choice: ConnectChoice) => {
    if (choice.kind === "lemma") {
      goTo("start");
      return;
    }

    setIsConnectingAi(true);
    try {
      const organization = await ensureOrganization(
        audience ?? "personal",
      );
      if (!organization) {
        toast.error("Could not prepare your workspace");
        return;
      }

      const profile = await createAgentRuntime.mutateAsync({
        organizationId: organization.id,
        request:
          choice.providerKind === "openai"
            ? {
                source: "OPENAI_COMPATIBLE",
                name: choice.name,
                base_url: choice.baseUrl,
                api_key: choice.apiKey || null,
                default_model_name: choice.defaultModelName,
                model_names: choice.modelNames,
              }
            : {
                source: "ANTHROPIC_COMPATIBLE",
                name: choice.name,
                base_url: choice.baseUrl || null,
                api_key: choice.apiKey,
                default_model_name: choice.defaultModelName,
                model_names: choice.modelNames,
              },
      });
      setConnectedProfileId(profile.id);
      toast.success(`${choice.name} saved`);

      if (basePod) {
        await updatePodDefaultRuntime.mutateAsync({
          podId: basePod.id,
          runtime: { profile_id: profile.id, model_name: null },
        });
      }
      goTo("start");
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Failed to connect AI";
      toast.error(message);
    } finally {
      setIsConnectingAi(false);
    }
  };

  const handleChooseStartPath = async (path: OnboardingStartPath) => {
    // Resolved before the pod exists, because it is what the pod gets called.
    // A pod restored from a draft keeps the name it already has — this only
    // ever names a pod being created right now.
    const launch =
      path === "templates" || path === "coding-agents" || path === "blank"
        ? null
        : startPathComposerLaunch(path);
    const pod =
      basePod ||
      (await createBasePod("personal", "", undefined, launch?.podName));
    if (!pod) return false;

    if (path === "templates") {
      clearOnboardingDraft();
      trackPodReady("new_org", signupAt);
      router.push(`/pod/${pod.id}`);
      return true;
    }
    if (path === "coding-agents") return false;

    if (connectedProfileId) {
      void updatePodDefaultRuntime.mutateAsync({
        podId: pod.id,
        runtime: { profile_id: connectedProfileId, model_name: null },
      });
    }

    clearOnboardingDraft();

    // Nothing to say on their behalf. The pod's own home already asks the
    // question better than a form could.
    if (!launch) {
      trackPodReady("new_org", signupAt);
      router.push(`/pod/${pod.id}`);
      return true;
    }

    trackPodReady("new_org", signupAt);
    router.push(
      buildComposerLaunchHref(pod.id, {
        draft: launch.stem,
        instructions: [
          FIRST_RUN_DELIGHT,
          launch.instructions,
          `The pod already exists: ${pod.name}. Do not create another pod. Build inside the current pod. Inspect existing resources first, reuse anything that fits, seed believable sample data, and wire any surface or connector that fits how they already work.`,
        ].join("\n\n"),
        metadata: {
          source: "onboarding",
          intent: launch.intent,
          first_run: true,
          pod_id: pod.id,
        },
      }),
    );
    return true;
  };

  if (step === "boot") {
    return (
      <SetupShell fullBleed>
        <div className="relative flex min-h-screen w-full flex-col overflow-hidden">
          <div className="setup-card-glow absolute inset-0" />
          {/* Country-skyline morph is disabled for now — revisit once the
              transition into the split-view steps is settled. */}
          {/* <IntroSkylines /> */}
          <div className="relative z-10 flex flex-1 flex-col px-5 py-5 sm:px-7 sm:py-6">
            <SetupChrome />
            <div className="mx-auto flex flex-1 max-w-4xl flex-col items-center justify-center pb-16">
              <AnomalousOrb className="static mb-8 h-40 w-40 shrink-0 sm:h-48 sm:w-48" />
              <BootStep onBegin={handleBegin} />
            </div>
          </div>
        </div>
      </SetupShell>
    );
  }

  const orderedSteps = setupStepsForAudience(audience, isLocal).filter(
    (candidate) => candidate !== "workspace" || !activeOrganization,
  );
  const previousStep = previousOnboardingStep(orderedSteps, step);
  const handleBack = () => {
    if (!previousStep) return;
    goTo(previousStep);
  };
  const onBack = previousStep ? handleBack : undefined;

  return (
    <SetupShell fullBleed>
      {step === "identity" ? (
        <IdentityStep
          email={email}
          name={identityName}
          domain={normalizedWorkDomain || null}
          workspaceName={workspaceName}
          organization={
            activeOrganization || suggestedOrganization || null
          }
          organizationAction={
            activeOrganization
              ? "continue"
              : suggestedOrganization
                ? "join"
                : "create"
          }
          isResolvingWorkspace={suggestedOrganizations.isLoading}
          isSaving={
            updateProfile.isPending ||
            joinSuggestedOrganization.isPending ||
            createOrganization.isPending
          }
          onNameChange={setIdentityName}
          onSubmit={handleIdentitySubmit}
          onBack={onBack}
          steps={orderedSteps}
        />
      ) : step === "audience" ? (
        <AudienceStep
          audience={audience}
          isSaving={isCreatingPod}
          savingAudience="personal"
          onSelect={handleAudienceSelect}
          onBack={onBack}
          steps={orderedSteps}
        />
      ) : step === "team" ? (
        <TeamStep
          teamKind={teamKind}
          customTeamName={customTeamName}
          isCreating={isCreatingPod}
          onTeamKindChange={(value) => {
            setTeamKind(value);
            saveOnboardingDraft({ teamKind: value });
          }}
          onCustomTeamNameChange={(value) => {
            setCustomTeamName(value);
            saveOnboardingDraft({ customTeamName: value });
          }}
          onContinue={handleTeamContinue}
          onBack={onBack}
          steps={orderedSteps}
        />
      ) : step === "workspace" ? (
        <WorkspaceStep
          domain={workDomain || null}
          suggestedOrganization={suggestedOrganization}
          workspaceName={workspaceName}
          allowDomainJoin={allowDomainJoin}
          isJoining={joinSuggestedOrganization.isPending}
          isCreating={createOrganization.isPending || isCreatingPod}
          onWorkspaceNameChange={(value) => {
            setWorkspaceName(value);
            saveOnboardingDraft({ workspaceName: value });
          }}
          onAllowDomainJoinChange={(value) => {
            setAllowDomainJoin(value);
            saveOnboardingDraft({ allowDomainJoin: value });
          }}
          onJoinSuggested={() => void handleJoinSuggested()}
          onCreateWorkspace={() => void handleCreateWorkspace()}
          onBack={onBack}
          steps={orderedSteps}
        />
      ) : step === "connect" ? (
        <ConnectStep
          isSaving={isConnectingAi}
          onContinue={handleConnectContinue}
          onBack={onBack}
          steps={orderedSteps}
        />
      ) : step === "intelligence" ? (
        <LocalIntelligenceStep
          organizationId={activeOrganization?.id ?? null}
          onContinue={handleLocalIntelligenceContinue}
          onBack={onBack}
          steps={orderedSteps}
        />
      ) : step === "sharing" ? (
        <LocalSharingStep
          onContinue={() => goTo("start")}
          onBack={onBack}
          steps={orderedSteps}
        />
      ) : (
        <StartStep
          isCreating={isCreatingPod}
          onChoosePath={handleChooseStartPath}
          onBack={onBack}
          steps={orderedSteps}
        />
      )}
    </SetupShell>
  );
}
