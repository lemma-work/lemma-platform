"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { LoadingState } from "@/components/brand/loader";
import { useOrganization } from "@/components/dashboard/org-context";
import { AnomalousOrb } from "@/components/ui/anomalous-orb";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import {
  readLastOpenedPodId,
  subscribeToLastOpenedPodId,
} from "@/lib/pods/last-opened-pod";
import {
  readOnboardingSkippedFirstPod,
  subscribeToOnboardingSkippedFirstPod,
  markOnboardingSkippedFirstPod,
} from "@/lib/pods/onboarding-skip";
import {
  useCreateOrganization,
  useJoinSuggestedOrganization,
  useMyOrganizationInvitations,
  useOrganizationSlugAvailability,
  useSuggestedOrganizations,
} from "@/lib/hooks/use-organizations";
import { useAccessiblePods } from "@/lib/hooks/use-pods";
import { useProfile, useUpdateProfile } from "@/lib/hooks/use-user";
import {
  useCreateAgentRuntime,
  useUpdatePodDefaultAgentRuntime,
} from "@/lib/hooks/use-agent-runtime";
import { RuntimeProfileScope } from "lemma-sdk";
import {
  OrganizationInvitationStatus,
  OrganizationJoinPolicy,
  type Organization,
} from "@/lib/types";
import {
  normalizeEmailDomain,
  slugifyOrganizationName,
  workDomainFromEmail,
} from "@/lib/utils/organization-slugs";
import {
  FIRST_RUN_DELIGHT,
  buildRecipeConversationHref,
  getRecipeById,
} from "@/lib/recipes/recipes";

import {
  ProgressDots,
  SetupChrome,
  SetupShell,
} from "./account-onboarding-chrome";
import {
  buildPodDescription,
  buildPromptFromIntent,
  defaultWorkspaceName,
  derivePodNameFromIntent,
  inferFullName,
  personalWorkspaceName,
  setupStepsForAudience,
  splitName,
  startRecipesForAudience,
  type Audience,
  type ConnectChoice,
  type SetupStep,
} from "./account-onboarding-helpers";
import {
  AudienceStep,
  BootStep,
  ConnectStep,
  IdentityStep,
  IntroSkylines,
  InvitationsStep,
  StartStep,
  WorkspaceStep,
} from "./account-onboarding-steps";

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
  const isProfileComplete = Boolean(profile?.first_name?.trim());
  const lastOpenedPodId = useSyncExternalStore(
    subscribeToLastOpenedPodId,
    readLastOpenedPodId,
    () => null,
  );
  const hasLastOpenedPod = requireFirstPod && Boolean(lastOpenedPodId);
  const skippedFirstPod = useSyncExternalStore(
    subscribeToOnboardingSkippedFirstPod,
    readOnboardingSkippedFirstPod,
    () => null,
  );
  const hasSkippedFirstPod = requireFirstPod && Boolean(skippedFirstPod);
  const { data: podsData, isLoading: isLoadingPods } = useAccessiblePods({
    enabled: requireFirstPod && !hasLastOpenedPod && !hasSkippedFirstPod,
  });
  const pods = podsData?.items || [];
  const { data: invitationsData, isLoading: isLoadingInvitations } =
    useMyOrganizationInvitations(OrganizationInvitationStatus.PENDING, {
      enabled: isProfileComplete,
    });
  const pendingInvitations = invitationsData?.items || [];
  const needsProfile = Boolean(profile) && !isProfileComplete;
  const needsInvitations = isProfileComplete && pendingInvitations.length > 0;
  const needsOrganization =
    isProfileComplete && !isLoadingOrganizations && organizations.length === 0;
  const needsFirstPod =
    requireFirstPod &&
    !hasLastOpenedPod &&
    !hasSkippedFirstPod &&
    isProfileComplete &&
    !needsOrganization &&
    !isLoadingPods &&
    pendingInvitations.length === 0 &&
    organizations.length > 0 &&
    pods.length === 0;
  const [setupActive, setSetupActive] = useState(false);
  const nextSetupStep: SetupStep = needsProfile
    ? "identity"
    : needsOrganization
      ? "audience"
      : needsFirstPod
        ? "start"
        : "audience";
  const setupInitialStep: SetupStep =
    setupActive || needsFirstPod ? nextSetupStep : "boot";

  if (
    !setupActive &&
    (isLoadingProfile ||
      isLoadingOrganizations ||
      (isProfileComplete && requireFirstPod && !hasLastOpenedPod && isLoadingPods) ||
      (isProfileComplete && isLoadingInvitations))
  ) {
    if (preflightFallback) {
      return preflightFallback;
    }

    return (
      <SetupShell>
        <LoadingState
          title="Preparing your workspace"
          description="Checking identity, workspace, invitations, and pods."
          shape="lines"
          className="w-full max-w-xl"
        />
      </SetupShell>
    );
  }

  if (needsInvitations) {
    return <InvitationsStep invitations={pendingInvitations} />;
  }

  if (needsProfile || needsOrganization || needsFirstPod || setupActive) {
    return (
      <SetupAssistant
        profile={profile}
        organizations={organizations}
        initialOrganization={currentOrg || organizations[0] || null}
        initialAudience={organizations.length > 0 ? "team" : null}
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
  initialOrganization,
  initialAudience,
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
  } | null;
  organizations: Organization[];
  initialOrganization: Organization | null;
  initialAudience: Audience | null;
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
  const suggestedOrganizations = useSuggestedOrganizations({
    enabled: Boolean(profile?.email) && organizations.length === 0,
  });
  const suggestedOrganization = suggestedOrganizations.data?.items?.[0] || null;
  const email = profile?.email || "";
  const workDomain = workDomainFromEmail(email);
  const normalizedWorkDomain = normalizeEmailDomain(workDomain);
  const inferredName = inferFullName(profile);
  const [step, setStep] = useState<SetupStep>(initialStep);
  const [createdOrganization, setCreatedOrganization] =
    useState<Organization | null>(null);
  const [isCreatingPod, setIsCreatingPod] = useState(false);
  const [isConnectingAi, setIsConnectingAi] = useState(false);
  const [connectedProfileId, setConnectedProfileId] = useState<string | null>(
    null,
  );
  const [identityName, setIdentityName] = useState(inferredName);
  const [workspaceName, setWorkspaceName] = useState(
    defaultWorkspaceName(inferredName),
  );
  const [audience, setAudience] = useState<Audience | null>(initialAudience);
  const startRecipes = useMemo(
    () => startRecipesForAudience(audience ?? "personal"),
    [audience],
  );
  const [selectedRecipeId, setSelectedRecipeId] = useState(
    () => startRecipesForAudience(initialAudience ?? "personal")[0]?.id ?? "",
  );
  const [customIntent, setCustomIntent] = useState("");
  const [allowDomainJoin, setAllowDomainJoin] = useState(
    Boolean(normalizedWorkDomain),
  );
  const slug = useMemo(
    () => slugifyOrganizationName(workspaceName),
    [workspaceName],
  );
  const slugAvailability = useOrganizationSlugAvailability(slug, {
    enabled: step === "workspace" && !suggestedOrganization && slug.length > 2,
  });
  const activeOrganization = createdOrganization || initialOrganization;

  useEffect(() => {
    if (step === "boot" && initialStep !== "boot") {
      setStep(initialStep);
    }
  }, [initialStep, step]);

  useEffect(() => {
    setAllowDomainJoin(Boolean(normalizedWorkDomain));
  }, [normalizedWorkDomain]);

  const goTo = (nextStep: SetupStep) => {
    onSetupStart();
    setStep(nextStep);
  };

  const handleBegin = () => {
    goTo(startStep);
  };

  const handleIdentitySubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const parsed = splitName(identityName);
    if (!parsed.firstName) return;

    updateProfile.mutate(
      {
        first_name: parsed.firstName,
        last_name: parsed.lastName || null,
      },
      {
        onSuccess: () => {
          toast.success("Operator profile saved");
          setWorkspaceName(defaultWorkspaceName(identityName));
          goTo("audience");
        },
        onError: (error) =>
          toast.error(`Failed to save profile: ${error.message}`),
      },
    );
  };

  const handleAudienceSelect = (value: Audience) => {
    setAudience(value);
    setCustomIntent("");
    setSelectedRecipeId(startRecipesForAudience(value)[0]?.id ?? "");
    // Solo users skip workspace setup entirely — their workspace is created
    // silently when the first pod lands.
    goTo(value === "team" ? "workspace" : "connect");
  };

  const handleJoinSuggested = () => {
    if (!suggestedOrganization) return;

    joinSuggestedOrganization.mutate(suggestedOrganization.id, {
      onSuccess: (organization) => {
        toast.success(`Joined ${organization.name}`);
        setCreatedOrganization(organization);
        onOrganizationReady(organization);
        // Members joining an existing workspace may already have accessible
        // pods. Route to home and let the root gate decide whether to open an
        // existing pod or fall through to pod setup, instead of always forcing
        // first-pod creation.
        router.replace("/home");
      },
      onError: (error) =>
        toast.error(`Could not join workspace: ${error.message}`),
    });
  };

  const handleCreateWorkspace = () => {
    const useDomainJoin = allowDomainJoin && Boolean(normalizedWorkDomain);
    createOrganization.mutate(
      {
        name: workspaceName.trim(),
        join_policy: useDomainJoin
          ? OrganizationJoinPolicy.EMAIL_DOMAIN
          : OrganizationJoinPolicy.INVITE_ONLY,
        email_domain: useDomainJoin ? normalizedWorkDomain : null,
      },
      {
        onSuccess: (organization) => {
          toast.success(`${organization.name} created`);
          setCreatedOrganization(organization);
          onOrganizationReady(organization);
          goTo("connect");
        },
        onError: (error) =>
          toast.error(`Failed to create workspace: ${error.message}`),
      },
    );
  };

  // Solo users never name a workspace. Make sure one exists before the pod
  // lands, creating it quietly if needed.
  const ensureOrganization = async (): Promise<Organization | null> => {
    if (activeOrganization) return activeOrganization;

    const organization = await createOrganization.mutateAsync({
      name: personalWorkspaceName(identityName),
      join_policy: OrganizationJoinPolicy.INVITE_ONLY,
      email_domain: null,
    });
    setCreatedOrganization(organization);
    onOrganizationReady(organization);
    return organization;
  };

  const handleConnectContinue = async (choice: ConnectChoice) => {
    if (choice.kind === "lemma") {
      goTo("start");
      return;
    }

    setIsConnectingAi(true);
    try {
      const organization = await ensureOrganization();
      if (!organization) {
        toast.error("Could not prepare your workspace");
        return;
      }

      if (choice.kind === "daemon") {
        const profile = await createAgentRuntime.mutateAsync({
          organizationId: organization.id,
          request: {
            source: "USER_DAEMON",
            daemon_id: choice.daemonId,
            harness_kind: choice.harnessKind,
            scope: RuntimeProfileScope.PERSONAL,
            name: `${choice.displayName} daemon`,
            default_model_name: choice.modelName || undefined,
          },
        });
        setConnectedProfileId(profile.id);
        toast.success(`${choice.displayName} connected`);
      } else {
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

  const handleSkipFirstPod = () => {
    markOnboardingSkippedFirstPod();
    router.replace("/home");
  };

  const handleCreateFromStart = async () => {
    // A typed brief always wins over a preselected card.
    const intentText = customIntent.trim();
    const recipe = intentText ? null : getRecipeById(selectedRecipeId);
    if (!intentText && !recipe) {
      toast.error("Describe what you want, or pick a starting point");
      return;
    }

    setIsCreatingPod(true);
    try {
      const organization = await ensureOrganization();
      if (!organization) {
        toast.error("Could not prepare your workspace");
        return;
      }

      if (recipe) {
        const pod = await getLemmaClient().pods.create({
          name: recipe.name,
          description: recipe.blurb,
          organization_id: organization.id,
        });
        toast.success(`${pod.name} created`);
        queryClient.invalidateQueries({ queryKey: ["pods"] });
        if (connectedProfileId) {
          await updatePodDefaultRuntime.mutateAsync({
            podId: pod.id,
            agentRuntimeId: connectedProfileId,
          });
        }
        router.push(
          buildRecipeConversationHref(pod.id, recipe, {
            podName: pod.name,
            mode: recipe.source.kind === "repo" ? "customize" : undefined,
            firstRun: true,
          }),
        );
        return;
      }

      const pod = await getLemmaClient().pods.create({
        name: derivePodNameFromIntent(intentText),
        description: buildPodDescription(intentText, "ai"),
        organization_id: organization.id,
      });
      toast.success(`${pod.name} created`);
      queryClient.invalidateQueries({ queryKey: ["pods"] });
      if (connectedProfileId) {
        await updatePodDefaultRuntime.mutateAsync({
          podId: pod.id,
          agentRuntimeId: connectedProfileId,
        });
      }
      const params = new URLSearchParams({
        assistantMessage: buildPromptFromIntent(intentText),
        conversationInstructions: [
          FIRST_RUN_DELIGHT,
          "Use the user-visible message as the goal. Propose and build the smallest useful first version, seed believable sample data, and wire any surface or connector that fits how they already work.",
        ].join("\n\n"),
        conversationMetadata: JSON.stringify({
          source: "onboarding",
          intent: "create_resource",
          first_run: true,
        }),
      });
      router.push(`/pod/${pod.id}/conversations/new?${params.toString()}`);
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Failed to create pod";
      toast.error(message);
    } finally {
      setIsCreatingPod(false);
    }
  };

  return (
    <SetupShell>
      <section className="setup-card-shell relative mx-auto w-full overflow-hidden backdrop-blur-xl">
        {step === "boot" ? <AnomalousOrb className="setup-card-orb" /> : null}
        <div className="setup-card-glow absolute inset-0" />
        {step === "boot" ? <IntroSkylines /> : null}
        <div className="relative z-10 min-h-[min(680px,calc(100vh-80px))] px-5 py-5 sm:px-7 sm:py-6">
          <SetupChrome intro={step === "boot"} />
          <div className="mx-auto flex min-h-[min(570px,calc(100vh-190px))] max-w-4xl flex-col items-center justify-center pt-8 pb-16">
            {step === "boot" ? (
              <BootStep onBegin={handleBegin} />
            ) : step === "identity" ? (
              <IdentityStep
                email={email}
                name={identityName}
                isSaving={updateProfile.isPending}
                onNameChange={setIdentityName}
                onSubmit={handleIdentitySubmit}
              />
            ) : step === "audience" ? (
              <AudienceStep audience={audience} onSelect={handleAudienceSelect} />
            ) : step === "workspace" ? (
              <WorkspaceStep
                domain={workDomain}
                suggestedOrganization={suggestedOrganization}
                workspaceName={workspaceName}
                slugAvailable={slugAvailability.data?.available}
                allowDomainJoin={allowDomainJoin}
                isJoining={joinSuggestedOrganization.isPending}
                isCreating={createOrganization.isPending}
                onWorkspaceNameChange={setWorkspaceName}
                onAllowDomainJoinChange={setAllowDomainJoin}
                onJoinSuggested={handleJoinSuggested}
                onCreateWorkspace={handleCreateWorkspace}
              />
            ) : step === "connect" ? (
              <ConnectStep
                isSaving={isConnectingAi}
                onContinue={handleConnectContinue}
              />
            ) : (
              <StartStep
                audience={audience ?? "personal"}
                recipes={startRecipes}
                selectedRecipeId={selectedRecipeId}
                customIntent={customIntent}
                isCreating={isCreatingPod}
                onSelectRecipe={(id) => {
                  setCustomIntent("");
                  setSelectedRecipeId(id);
                }}
                onCustomIntentChange={(value) => {
                  setCustomIntent(value);
                  // Typing overrides a card; clearing restores the default pick.
                  setSelectedRecipeId(
                    value.trim() ? "" : startRecipes[0]?.id ?? "",
                  );
                }}
                onContinue={handleCreateFromStart}
                onSkip={handleSkipFirstPod}
              />
            )}
          </div>
          <ProgressDots
            currentStep={step}
            steps={setupStepsForAudience(audience)}
          />
        </div>
      </section>
    </SetupShell>
  );
}
