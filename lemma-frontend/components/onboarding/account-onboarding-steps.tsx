import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  Check,
  CheckCircle2,
  KeyRound,
  Loader2,
  Mail,
  PackageOpen,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Terminal,
  UserRound,
  UsersRound,
} from "lucide-react";

import { LoadingState } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { HarnessKind } from "lemma-sdk";
import {
  type Organization,
  type OrganizationInvitation,
} from "@/lib/types";
import { useAcceptOrganizationInvitation } from "@/lib/hooks/use-organizations";
import { markOnboardingSkippedFirstPod } from "@/lib/pods/onboarding-skip";
import {
  getGitHubRepoLabel,
  getKitById,
  kitCatalog,
  type KitDefinition,
} from "@/lib/kits/catalog";
import { RECIPE_BUILDS_LABEL, type Recipe } from "@/lib/recipes/recipes";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useAvailableAgentRuntimeHarnesses } from "@/lib/hooks/use-agent-runtime";
import {
  availableHarnessKey,
  CUSTOM_PROVIDER_OPTIONS,
  firstHarnessModelName,
  HARNESS_LOGOS,
  isHarnessAvailable,
  splitModelNames,
  type CustomProviderKind,
} from "@/components/agents/agent-runtime-helpers";

import {
  SetupPanel,
  SetupPrimaryButton,
  SetupShell,
  SetupSplitPanel,
} from "./account-onboarding-chrome";
import {
  AudiencePreviewBody,
  ConnectPreviewBody,
  OnboardingPreviewChrome,
  StartPreviewBody,
  WorkspacePreviewBody,
} from "./onboarding-preview";
import {
  AUDIENCE_OPTIONS,
  BUILD_PATHS,
  DAEMON_SETUP_STEPS,
  INTENT_EXAMPLE_LABELS,
  INTENT_EXAMPLES,
  SETUP_GREETINGS,
  TEAM_OPTIONS,
  derivePodNameFromIntent,
  podNameForAudience,
  splitGraphemes,
  teamLabelForKind,
  type Audience,
  type BuildPath,
  type ConnectChoice,
  type SetupStep,
  type TeamKind,
} from "./account-onboarding-helpers";

type ProviderPreset = {
  id: string;
  title: string;
  providerKind: CustomProviderKind;
  baseUrl: string;
  name: string;
  defaultModelName?: string;
};

const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "openai",
    title: "OpenAI",
    providerKind: "openai",
    baseUrl: "https://api.openai.com/v1",
    name: "OpenAI",
  },
  {
    id: "anthropic",
    title: "Anthropic",
    providerKind: "anthropic",
    baseUrl: "https://api.anthropic.com",
    name: "Anthropic",
  },
  {
    id: "openrouter",
    title: "OpenRouter",
    providerKind: "openai",
    baseUrl: "https://openrouter.ai/api/v1",
    name: "OpenRouter",
  },
  {
    id: "fireworks",
    title: "Fireworks",
    providerKind: "openai",
    baseUrl: "https://api.fireworks.ai/inference/v1",
    name: "Fireworks",
  },
  {
    id: "custom",
    title: "Custom",
    providerKind: "openai",
    baseUrl: "",
    name: "",
  },
];

export function InvitationsStep({
  invitations,
}: {
  invitations: OrganizationInvitation[];
}) {
  const router = useRouter();
  const firstInvitation = invitations[0];
  const hasSubmittedRef = useRef(false);
  const { mutate: acceptInvitation } = useAcceptOrganizationInvitation();

  useEffect(() => {
    if (!firstInvitation || hasSubmittedRef.current) return;

    hasSubmittedRef.current = true;
    acceptInvitation(firstInvitation.id, {
      onSuccess: (response) => {
        markOnboardingSkippedFirstPod();
        const destination =
          response.redirect_uri || firstInvitation.redirect_uri || "/";

        if (/^https?:\/\//i.test(destination)) {
          window.location.assign(destination);
          return;
        }

        router.replace(destination.startsWith("/") ? destination : `/${destination}`);
      },
      onError: (error) => {
        toast.error(`Could not join invitation: ${error.message}`);
        router.replace(`/invitations/${firstInvitation.id}/accept`);
      },
    });
  }, [acceptInvitation, firstInvitation, router]);

  return (
    <SetupShell>
      <LoadingState
        title="Joining your workspace"
        description="Accepting your invitation so setup can wait until later."
        shape="lines"
        className="w-full max-w-xl"
      />
    </SetupShell>
  );
}

// Static and immediately visible on purpose — the previous version staged a
// multilingual morphing greeting + skyline reveal ahead of this content on a
// ~7s timer tuned for the old boxed card layout. Disabled for now: revisit
// once the full-bleed shell settles.
export function BootStep({ onBegin }: { onBegin: () => void }) {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col items-center text-center">
      <h1 className="setup-boot-title font-normal tracking-normal text-[var(--text-primary)]">
        Welcome to your AI workspace
      </h1>
      <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-[var(--text-secondary)]">
        Tell Lemma what you want done and it builds the space around it — bots,
        apps, the lot. Or just poke around. Nothing to set up first.
      </p>
      <Button
        onClick={onBegin}
        size="lg"
        className="setup-primary-action mt-8 h-12 min-w-56 gap-3 text-sm font-medium"
      >
        <Sparkles className="h-5 w-5" />
        Begin setup
      </Button>
      <p className="mx-auto mt-4 max-w-sm font-mono text-xs text-[var(--text-tertiary)]">
        Or run{" "}
        <span className="text-[var(--text-secondary)]">lemma init</span>
      </p>
    </div>
  );
}

export function IntroSkylines() {
  return (
    <div className="setup-skyline-stage" aria-hidden="true">
      {SETUP_GREETINGS.map((greeting) => (
        <Image
          key={`${greeting.text}-skyline`}
          src={greeting.skyline}
          alt=""
          width={2172}
          height={487}
          sizes="(max-width: 768px) 92vw, 920px"
          className={["setup-country-skyline", greeting.skylineClassName].join(
            " ",
          )}
        />
      ))}
    </div>
  );
}

export function GreetingPrelude() {
  return (
    <div className="setup-greeting-prelude" aria-hidden="true">
      {SETUP_GREETINGS.map((greeting) => (
        <div
          key={greeting.text}
          className={["setup-morph-word", greeting.className].join(" ")}
          lang={greeting.lang}
        >
          {splitGraphemes(greeting.text).map((letter, index) => (
            <span
              key={`${greeting.text}-${letter}-${index}`}
              className={[
                "setup-morph-letter",
                `setup-morph-letter-${index % 10}`,
              ].join(" ")}
            >
              {letter}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

export function IdentityStep({
  email,
  name,
  isSaving,
  onNameChange,
  onSubmit,
  onBack,
  steps,
}: {
  email: string;
  name: string;
  isSaving: boolean;
  onNameChange: (value: string) => void;
  onSubmit: (event: React.FormEvent) => void;
  onBack?: () => void;
  steps?: SetupStep[];
}) {
  return (
    <SetupSplitPanel
      title="What should Lemma call you?"
      subtitle="We will use this to set up your operator profile and find your team."
      onBack={onBack}
      currentStep="identity"
      steps={steps}
      preview={
        <OnboardingPreviewChrome orgLabel="Your workspace" personName={name}>
          <div className="setup-preview-card">
            <p className="setup-preview-card-title">
              {name.trim() ? `Welcome, ${name.trim()}` : "Welcome"}
            </p>
            <p className="mt-1.5 text-xs leading-5 text-[var(--text-tertiary)]">
              This is what teammates will see when you sign in.
            </p>
          </div>
        </OnboardingPreviewChrome>
      }
    >
      <form onSubmit={onSubmit} className="w-full max-w-xl space-y-5 text-left">
        <div className="space-y-2">
          <Label htmlFor="operator-name">Full name</Label>
          <div className="form-field-control flex h-14 items-center gap-3 px-4">
            <UserRound className="h-5 w-5 text-[var(--text-tertiary)]" />
            <input
              id="operator-name"
              value={name}
              onChange={(event) => onNameChange(event.target.value)}
              className="inline-edit-field min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
              placeholder="Ada Lovelace"
              autoComplete="name"
              required
            />
          </div>
        </div>
        {email ? (
          <p className="flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
            <Mail className="h-4 w-4" />
            Signed in as {email}
          </p>
        ) : null}
        <SetupPrimaryButton
          type="submit"
          loading={isSaving}
          loadingLabel="Saving profile"
          className="!mx-0"
        >
          Continue
        </SetupPrimaryButton>
      </form>
    </SetupSplitPanel>
  );
}

export function AudienceStep({
  audience,
  isSaving = false,
  savingAudience = null,
  onSelect,
  onBack,
  steps,
}: {
  audience: Audience | null;
  isSaving?: boolean;
  savingAudience?: Audience | null;
  onSelect: (audience: Audience) => void;
  onBack?: () => void;
  steps?: SetupStep[];
}) {
  // Selecting an audience navigates to the next step immediately, so hover
  // is the only chance to actually see the other option's preview — clicking
  // never leaves it on screen long enough to look at.
  const [hoveredAudience, setHoveredAudience] = useState<Audience | null>(
    null,
  );
  const previewAudience = hoveredAudience ?? audience;

  return (
    <SetupSplitPanel
      title="Who are you setting this up for?"
      subtitle="This shapes how much we set up up front. You can change direction later."
      preview={<AudiencePreviewBody audience={previewAudience} />}
      onBack={onBack}
      currentStep="audience"
      steps={steps}
    >
      <div className="grid w-full max-w-2xl gap-3 text-left sm:grid-cols-2">
        {AUDIENCE_OPTIONS.map((option) => {
          const Icon = option.icon;
          const selected = audience === option.id;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => onSelect(option.id)}
              disabled={isSaving}
              onMouseEnter={() => setHoveredAudience(option.id)}
              onMouseLeave={() => setHoveredAudience(null)}
              onFocus={() => setHoveredAudience(option.id)}
              onBlur={() => setHoveredAudience(null)}
              data-active={selected}
              className={[
                "setup-path-choice flex w-full items-start gap-3 px-4 py-4 text-left",
                selected ? "is-active" : "",
              ].join(" ")}
            >
              <span
                className={[
                  "setup-path-choice-icon flex h-9 w-9 shrink-0 items-center justify-center",
                  selected ? "is-active" : "",
                ].join(" ")}
              >
                <Icon className="h-4 w-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                  {option.title}
                  {selected ? <Check className="h-4 w-4" /> : null}
                  {isSaving && savingAudience === option.id ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : null}
                </span>
                <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">
                  {option.description}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </SetupSplitPanel>
  );
}

export function TeamStep({
  teamKind,
  customTeamName,
  isCreating,
  onTeamKindChange,
  onCustomTeamNameChange,
  onContinue,
  onBack,
  steps,
}: {
  teamKind: TeamKind | null;
  customTeamName: string;
  isCreating: boolean;
  onTeamKindChange: (teamKind: TeamKind) => void;
  onCustomTeamNameChange: (value: string) => void;
  onContinue: () => void;
  onBack?: () => void;
  steps?: SetupStep[];
}) {
  const teamLabel = teamLabelForKind(teamKind, customTeamName);
  const podTitle = podNameForAudience("team", teamLabel);
  const canContinue = teamKind !== "other" || Boolean(customTeamName.trim());

  return (
    <SetupSplitPanel
      title="What team do you work in?"
      subtitle="This becomes the pod for that team's agents, apps, workflows, and operating data."
      preview={
        <StartPreviewBody
          podTitle={podTitle}
          podBlurb="A shared pod for this team's agents, apps, workflows, and operating data."
          justSelected={null}
        />
      }
      onBack={onBack}
      currentStep="team"
      steps={steps}
    >
      <div className="w-full max-w-3xl space-y-4 text-left">
        <div className="grid gap-2.5 sm:grid-cols-2">
          {TEAM_OPTIONS.map((option) => {
            const Icon = option.icon;
            const selected = teamKind === option.id;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => onTeamKindChange(option.id)}
                data-active={selected}
                className={[
                  "setup-path-choice flex w-full items-start gap-3 px-4 py-4 text-left",
                  selected ? "is-active" : "",
                ].join(" ")}
              >
                <span
                  className={[
                    "setup-path-choice-icon flex h-9 w-9 shrink-0 items-center justify-center",
                    selected ? "is-active" : "",
                  ].join(" ")}
                >
                  <Icon className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                    {option.title}
                    {selected ? <Check className="h-4 w-4" /> : null}
                  </span>
                  <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">
                    {option.description}
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        {teamKind === "other" ? (
          <div className="space-y-2">
            <Label htmlFor="team-name">Team name</Label>
            <Input
              id="team-name"
              value={customTeamName}
              onChange={(event) => onCustomTeamNameChange(event.target.value)}
              placeholder="Community"
              autoFocus
            />
          </div>
        ) : null}

        <SetupPrimaryButton
          onClick={onContinue}
          loading={isCreating}
          loadingLabel={`Creating ${podTitle}`}
          disabled={isCreating || !canContinue}
          className="!mx-0"
        >
          Create {podTitle}
        </SetupPrimaryButton>
      </div>
    </SetupSplitPanel>
  );
}

export function ConnectStep({
  isSaving,
  onContinue,
  onBack,
  steps,
}: {
  isSaving: boolean;
  onContinue: (choice: ConnectChoice) => void;
  onBack?: () => void;
  steps?: SetupStep[];
}) {
  const [selectedOption, setSelectedOption] = useState<
    "lemma" | "daemon" | "provider"
  >("lemma");
  // Hovering a card previews it on the right without expanding its form —
  // clicking still does that (and selects it) separately.
  const [hoveredOption, setHoveredOption] = useState<
    "lemma" | "daemon" | "provider" | null
  >(null);
  const {
    data: harnessesData,
    isLoading: isLoadingHarnesses,
    refetch: refetchHarnesses,
    isRefetching: isRefetchingHarnesses,
  } = useAvailableAgentRuntimeHarnesses();
  const harnesses = harnessesData?.items ?? [];
  const availableLocalHarnesses = harnesses.filter(
    (h) => h.harness_kind !== HarnessKind.LEMMA && isHarnessAvailable(h),
  );

  const [selectedHarnessKey, setSelectedHarnessKey] = useState<string | null>(
    null,
  );
  const [selectedModel, setSelectedModel] = useState<string | null>(null);

  const [providerKind, setProviderKind] = useState<CustomProviderKind>("openai");
  const [providerName, setProviderName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelNames, setModelNames] = useState("");
  const [defaultModelName, setDefaultModelName] = useState("");

  const handleContinue = () => {
    if (selectedOption === "lemma") {
      onContinue({ kind: "lemma" });
      return;
    }

    if (selectedOption === "daemon") {
      const harness = availableLocalHarnesses.find(
        (h) => availableHarnessKey(h) === selectedHarnessKey,
      );
      if (!harness || !harness.daemon_id) return;
      onContinue({
        kind: "daemon",
        daemonId: harness.daemon_id,
        harnessKind: harness.harness_kind,
        displayName: harness.display_name,
        modelName: selectedModel ?? firstHarnessModelName(harness) ?? null,
      });
      return;
    }

    const name = providerName.trim();
    const url = baseUrl.trim();
    const key = apiKey.trim();
    const models = splitModelNames(modelNames);
    const defaultModel = defaultModelName.trim() || models[0];
    if (!name || !key || (providerKind === "openai" && !url)) return;
    onContinue({
      kind: "provider",
      providerKind,
      name,
      baseUrl: url,
      apiKey: key,
      modelNames: models,
      defaultModelName: defaultModel || undefined,
    });
  };

  const daemonCanContinue =
    selectedOption !== "daemon" ||
    Boolean(
      availableLocalHarnesses.find(
        (h) => availableHarnessKey(h) === selectedHarnessKey,
      )?.daemon_id,
    );
  const providerCanContinue =
    selectedOption !== "provider" ||
    (Boolean(providerName.trim()) &&
      Boolean(apiKey.trim()) &&
      (providerKind === "anthropic" || Boolean(baseUrl.trim())));
  const continueDisabled = isSaving || !daemonCanContinue || !providerCanContinue;

  const selectedHarness = availableLocalHarnesses.find(
    (h) => availableHarnessKey(h) === selectedHarnessKey,
  );
  const previewOption = hoveredOption ?? selectedOption;
  const previewModelName =
    previewOption === "daemon"
      ? (selectedModel ??
          (selectedHarness ? firstHarnessModelName(selectedHarness) : null) ??
          null)
      : previewOption === "provider"
        ? defaultModelName.trim() || splitModelNames(modelNames)[0] || null
        : null;
  const previewHarnesses = harnesses.map((h) => ({
    kind: h.harness_kind,
    detected: isHarnessAvailable(h),
  }));

  return (
    <SetupSplitPanel
      title="Connect your AI"
      subtitle="Choose how Lemma runs AI for you. You can change this anytime in settings."
      preview={
        <ConnectPreviewBody
          selectedOption={previewOption}
          harnesses={previewHarnesses}
          selectedHarnessKind={selectedHarness?.harness_kind}
          providerName={providerName}
          modelName={previewModelName}
        />
      }
      onBack={onBack}
      currentStep="connect"
      steps={steps}
    >
      <div className="w-full max-w-2xl space-y-3 text-left">
        <ConnectOptionCard
          selected={selectedOption === "daemon"}
          onClick={() => setSelectedOption("daemon")}
          onHoverChange={(hovering) => setHoveredOption(hovering ? "daemon" : null)}
          icon={<Terminal className="h-4 w-4" />}
          title="Connect a local harness"
          subtitle="Codex, Claude Code, or OpenCode via the Lemma daemon."
        />

        {selectedOption === "daemon" ? (
          <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-4 py-4">
            {availableLocalHarnesses.length > 0 ? (
              <div className="space-y-2">
                <p className="text-sm font-semibold text-[var(--text-primary)]">
                  Detected harnesses
                </p>
                {availableLocalHarnesses.map((harness) => {
                  const key = availableHarnessKey(harness);
                  const isSelected = selectedHarnessKey === key;
                  const models = harness.models ?? [];
                  return (
                    <div key={key}>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedHarnessKey(key);
                          setSelectedModel(models[0] ?? null);
                        }}
                        className={cn(
                          "agent-runtime-harness-button flex w-full items-center gap-2 rounded-md border px-3 py-2.5 text-left transition",
                          isSelected
                            ? "border-[var(--action-primary)] bg-[var(--action-primary-soft)]"
                            : "border-[var(--border-subtle)] hover:bg-[var(--surface-1)]",
                        )}
                      >
                        {HARNESS_LOGOS[harness.harness_kind] ? (
                          <Image
                            src={HARNESS_LOGOS[harness.harness_kind]!}
                            alt=""
                            width={16}
                            height={16}
                            className="h-4 w-4 object-contain"
                          />
                        ) : null}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-[var(--text-primary)]">
                            {harness.display_name}
                          </span>
                          {models.length > 0 ? (
                            <span className="block truncate font-mono text-xs text-[var(--text-tertiary)]">
                              {models.length} model{models.length > 1 ? "s" : ""}
                            </span>
                          ) : null}
                        </span>
                        {isSelected ? (
                          <Check className="h-4 w-4 shrink-0 text-[var(--action-primary)]" />
                        ) : null}
                      </button>
                      {isSelected && models.length > 1 ? (
                        <div className="mt-1.5 flex flex-wrap gap-1.5 px-1">
                          {models.map((model) => (
                            <button
                              key={model}
                              type="button"
                              onClick={() => setSelectedModel(model)}
                              className={cn(
                                "chip rounded-full border px-2.5 py-1 text-xs transition",
                                selectedModel === model
                                  ? "border-[var(--action-primary)] bg-[var(--action-primary-soft)] text-[var(--action-primary)]"
                                  : "border-[var(--border-subtle)] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]",
                              )}
                            >
                              {model}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div>
                <div className="flex items-start gap-3">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-tertiary)]">
                    <Terminal className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-[var(--text-primary)]">
                      {isLoadingHarnesses
                        ? "Checking for local harnesses…"
                        : "No harness detected yet"}
                    </p>
                    <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                      Install a harness and it shows up here automatically —
                      full setup steps are in the panel on the right.
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="h-7 shrink-0 gap-1.5 px-2"
                    onClick={() => void refetchHarnesses()}
                    disabled={isRefetchingHarnesses}
                  >
                    {isRefetchingHarnesses ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    Recheck
                  </Button>
                </div>
                {/* lg+ screens get the full step-by-step in the preview pane
                    on the right; below that breakpoint the pane is hidden, so
                    this is the only copy of the setup command. */}
                <code className="mt-4 block rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2 font-mono text-xs leading-5 text-[var(--text-primary)] lg:hidden">
                  {DAEMON_SETUP_STEPS.map((step) => step.command).join(" && ")}
                </code>
              </div>
            )}
          </div>
        ) : null}

        <ConnectOptionCard
          selected={selectedOption === "provider"}
          onClick={() => {
            setSelectedOption("provider");
            const preset = PROVIDER_PRESETS.find((p) => p.id !== "custom");
            if (!providerName && preset) setProviderName(preset.name);
            if (!baseUrl && preset) setBaseUrl(preset.baseUrl);
          }}
          onHoverChange={(hovering) => setHoveredOption(hovering ? "provider" : null)}
          icon={<KeyRound className="h-4 w-4" />}
          title="Paste an API key"
          subtitle="Bring your own OpenAI, Anthropic, OpenRouter, Fireworks, or other key."
        />

        {selectedOption === "provider" ? (
          <div className="space-y-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-4 py-4">
            <div>
              <p className="mb-2 text-xs font-medium text-[var(--text-tertiary)]">
                Quick picks
              </p>
              <div className="flex flex-wrap gap-2">
                {PROVIDER_PRESETS.map((preset) => {
                  const isActive =
                    preset.id !== "custom" &&
                    providerName === preset.name &&
                    baseUrl === preset.baseUrl &&
                    providerKind === preset.providerKind;
                  return (
                    <button
                      key={preset.id}
                      type="button"
                      onClick={() => {
                        setProviderKind(preset.providerKind);
                        setProviderName(preset.name);
                        setBaseUrl(preset.baseUrl);
                      }}
                      className={cn(
                        "chip rounded-full border px-3 py-1.5 text-xs font-medium transition",
                        isActive
                          ? "border-[var(--action-primary)] bg-[var(--action-primary-soft)] text-[var(--action-primary)]"
                          : "border-[var(--border-subtle)] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]",
                      )}
                    >
                      {preset.title}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex gap-2">
              {CUSTOM_PROVIDER_OPTIONS.map((option) => (
                <button
                  key={option.kind}
                  type="button"
                  onClick={() => {
                    setProviderKind(option.kind);
                  }}
                  className={cn(
                    "agent-runtime-scope-button flex-1 rounded-md border px-3 py-2 text-sm font-medium transition",
                    providerKind === option.kind
                      ? "border-[var(--action-primary)] bg-[var(--action-primary-soft)] text-[var(--action-primary)]"
                      : "border-[var(--border-subtle)] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]",
                  )}
                >
                  {option.title}
                </button>
              ))}
            </div>
            <div className="settings-field">
              <Label className="text-[var(--text-secondary)]">Name</Label>
              <Input
                value={providerName}
                onChange={(e) => setProviderName(e.target.value)}
                placeholder={providerKind === "openai" ? "OpenRouter" : "Anthropic"}
              />
            </div>
            <div className="settings-field">
              <Label className="text-[var(--text-secondary)]">Base URL</Label>
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={providerKind === "openai" ? "https://openrouter.ai/api/v1" : "https://api.anthropic.com"}
              />
            </div>
            <div className="settings-field">
              <Label className="text-[var(--text-secondary)]">API key</Label>
              <Input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
              />
            </div>
            <div className="settings-field">
              <Label className="text-[var(--text-secondary)]">
                Models{" "}
                <span className="font-normal text-[var(--text-tertiary)]">
                  (optional)
                </span>
              </Label>
              <textarea
                value={modelNames}
                onChange={(e) => setModelNames(e.target.value)}
                placeholder="one model per line"
                className="form-field-control min-h-20 w-full resize-y px-3 py-2 text-sm leading-5 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
              />
            </div>
            <div className="settings-field">
              <Label className="text-[var(--text-secondary)]">
                Default model{" "}
                <span className="font-normal text-[var(--text-tertiary)]">
                  (optional)
                </span>
              </Label>
              <Input
                value={defaultModelName}
                onChange={(e) => setDefaultModelName(e.target.value)}
                placeholder="First listed model is used by default"
              />
            </div>
          </div>
        ) : null}

        <ConnectOptionCard
          selected={selectedOption === "lemma"}
          onClick={() => setSelectedOption("lemma")}
          onHoverChange={(hovering) => setHoveredOption(hovering ? "lemma" : null)}
          icon={<Sparkles className="h-4 w-4" />}
          title="Use Lemma"
          subtitle="Fastest — no setup. AI runs on Lemma's built-in models."
        />

        <Button
          type="button"
          onClick={handleContinue}
          loading={isSaving}
          loadingLabel="Connecting"
          disabled={continueDisabled}
          className="setup-primary-action !flex mt-6 h-11 min-w-44 gap-2 px-6 text-sm font-medium"
        >
          Continue
          <ArrowRight className="h-4 w-4" />
        </Button>

        {selectedOption === "lemma" ? (
          <button
            type="button"
            onClick={() => onContinue({ kind: "lemma" })}
            className="setup-defer-button mt-1 block text-xs text-[var(--text-tertiary)] underline-offset-4 transition hover:text-[var(--text-secondary)] hover:underline"
          >
            Skip for now
          </button>
        ) : null}
      </div>
    </SetupSplitPanel>
  );
}

function ConnectOptionCard({
  selected,
  onClick,
  onHoverChange,
  icon,
  title,
  subtitle,
}: {
  selected: boolean;
  onClick: () => void;
  onHoverChange?: (hovering: boolean) => void;
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => onHoverChange?.(true)}
      onMouseLeave={() => onHoverChange?.(false)}
      onFocus={() => onHoverChange?.(true)}
      onBlur={() => onHoverChange?.(false)}
      data-active={selected}
      className={[
        "setup-path-choice flex w-full items-start gap-3 px-4 py-4 text-left",
        selected ? "is-active" : "",
      ].join(" ")}
    >
      <span
        className={[
          "setup-path-choice-icon flex h-9 w-9 shrink-0 items-center justify-center",
          selected ? "is-active" : "",
        ].join(" ")}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
          {title}
          {selected ? <Check className="h-4 w-4" /> : null}
        </span>
        <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">
          {subtitle}
        </span>
      </span>
    </button>
  );
}

export function StartStep({
  audience,
  podName,
  recipes,
  selectedRecipeId,
  customIntent,
  isCreating,
  onSelectRecipe,
  onCustomIntentChange,
  onBuildWithLemma,
  onContinue,
  onSkip,
  onBack,
  steps,
}: {
  audience: Audience;
  podName: string;
  recipes: Recipe[];
  selectedRecipeId: string;
  customIntent: string;
  isCreating: boolean;
  onSelectRecipe: (id: string) => void;
  onCustomIntentChange: (value: string) => void;
  onBuildWithLemma: () => void;
  onContinue: () => void;
  onSkip: () => void;
  onBack?: () => void;
  steps?: SetupStep[];
}) {
  const personal = audience === "personal";
  const hasIntent = Boolean(customIntent.trim());
  const continueDisabled = isCreating || (!hasIntent && !selectedRecipeId);
  const selectedRecipe = recipes.find((recipe) => recipe.id === selectedRecipeId);
  // Hovering a card previews it without committing — clicking still selects
  // it for real (and stays selected after the mouse leaves).
  const [hoveredRecipeId, setHoveredRecipeId] = useState<string | null>(null);
  const hoveredRecipe = recipes.find((recipe) => recipe.id === hoveredRecipeId);
  const previewTitle = hasIntent
    ? derivePodNameFromIntent(customIntent)
    : (hoveredRecipe?.name ?? selectedRecipe?.name ?? podName);
  const previewBlurb = hasIntent
    ? undefined
    : (hoveredRecipe?.blurb ?? selectedRecipe?.blurb);
  const primaryLabel = hasIntent
    ? "Build this"
    : selectedRecipe?.source.kind === "repo"
      ? "Install recipe"
      : "Build recipe";

  return (
    <SetupSplitPanel
      title="What should we add first?"
      subtitle={
        personal
          ? `${podName} is ready. Add a recipe, let Lemma build with you, or describe something specific.`
          : `${podName} is ready. Add a team recipe, let Lemma build with you, or describe the workflow you want.`
      }
      preview={
        <StartPreviewBody
          podTitle={previewTitle}
          podBlurb={previewBlurb}
          justSelected={hasIntent ? null : selectedRecipeId || null}
        />
      }
      onBack={onBack}
      currentStep="start"
      steps={steps}
    >
      <div className="w-full max-w-4xl text-left">
        <button
          type="button"
          onClick={onBuildWithLemma}
          className="setup-path-choice flex w-full items-start gap-3 px-4 py-4 text-left"
        >
          <span className="setup-path-choice-icon flex h-9 w-9 shrink-0 items-center justify-center">
            <Sparkles className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-[var(--text-primary)]">
              Build with Lemma
            </span>
            <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">
              Open the builder in this pod and let it propose the smallest useful first version.
            </span>
          </span>
          <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
        </button>

        <div className="mt-5 flex items-center gap-3 text-xs text-[var(--text-tertiary)]">
          <span className="h-px flex-1 bg-[var(--border-subtle)]" />
          describe something specific
          <span className="h-px flex-1 bg-[var(--border-subtle)]" />
        </div>

        <div className="form-field-control flex min-h-14 items-center gap-3 px-4 py-2">
          <Sparkles className="h-5 w-5 shrink-0 text-[var(--text-tertiary)]" />
          <input
            value={customIntent}
            onChange={(event) => onCustomIntentChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !continueDisabled) onContinue();
            }}
            className="inline-edit-field min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
            placeholder={
              personal
                ? "Log my meals from Telegram and let me ask how I ate this week"
                : "Triage support email from Gmail and draft replies for review"
            }
          />
        </div>

        <div className="mt-5 flex items-center gap-3 text-xs text-[var(--text-tertiary)]">
          <span className="h-px flex-1 bg-[var(--border-subtle)]" />
          or install a recipe
          <span className="h-px flex-1 bg-[var(--border-subtle)]" />
        </div>

        <div className="mt-4 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {recipes.map((recipe) => {
            const selected = !hasIntent && selectedRecipeId === recipe.id;
            return (
              <button
                key={recipe.id}
                type="button"
                onClick={() => onSelectRecipe(recipe.id)}
                onMouseEnter={() => setHoveredRecipeId(recipe.id)}
                onMouseLeave={() => setHoveredRecipeId(null)}
                onFocus={() => setHoveredRecipeId(recipe.id)}
                onBlur={() => setHoveredRecipeId(null)}
                data-active={selected}
                className={[
                  "setup-kit-option flex flex-col px-3.5 py-3.5 text-left",
                  selected ? "is-active" : "",
                ].join(" ")}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-[var(--text-primary)]">
                    {recipe.name}
                  </span>
                  {selected ? (
                    <Check className="h-4 w-4 shrink-0 text-[var(--text-primary)]" />
                  ) : null}
                </span>
                <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">
                  {recipe.blurb}
                </span>
                <span className="chip chip-sm mt-3 self-start font-mono text-[var(--text-tertiary)]">
                  {recipe.source.kind === "repo"
                    ? "GitHub kit"
                    : RECIPE_BUILDS_LABEL[recipe.builds]}
                </span>
              </button>
            );
          })}
        </div>

        <Button
          type="button"
          onClick={onContinue}
          loading={isCreating}
          loadingLabel={personal ? "Building your space" : "Creating pod"}
          disabled={continueDisabled}
          className="setup-primary-action !flex mt-6 h-11 min-w-44 gap-2 px-6 text-sm font-medium"
        >
          {primaryLabel}
          <ArrowRight className="h-4 w-4" />
        </Button>

        <button
          type="button"
          onClick={onSkip}
          disabled={isCreating}
          className="setup-defer-button mt-3 block text-xs text-[var(--text-tertiary)] underline-offset-4 transition hover:text-[var(--text-secondary)] hover:underline disabled:opacity-50"
        >
          I&apos;ll set this up later
        </button>
      </div>
    </SetupSplitPanel>
  );
}

export function WorkspaceStep({
  domain,
  suggestedOrganization,
  workspaceName,
  slugAvailable,
  allowDomainJoin,
  isJoining,
  isCreating,
  onWorkspaceNameChange,
  onAllowDomainJoinChange,
  onJoinSuggested,
  onCreateWorkspace,
  onBack,
  steps,
}: {
  domain: string | null;
  suggestedOrganization: Organization | null;
  workspaceName: string;
  slugAvailable?: boolean;
  allowDomainJoin: boolean;
  isJoining: boolean;
  isCreating: boolean;
  onWorkspaceNameChange: (value: string) => void;
  onAllowDomainJoinChange: (value: boolean) => void;
  onJoinSuggested: () => void;
  onCreateWorkspace: () => void;
  onBack?: () => void;
  steps?: SetupStep[];
}) {
  const [showManualCreate, setShowManualCreate] = useState(false);

  if (suggestedOrganization && !showManualCreate) {
    const teamDomain =
      suggestedOrganization.email_domain ||
      domain ||
      suggestedOrganization.slug;

    return (
      <>
        {onBack ? (
          <Button
            type="button"
            variant="ghost"
            onClick={onBack}
            className="fixed left-6 top-6 z-10 h-auto gap-1.5 px-0 text-sm text-[var(--text-tertiary)] hover:bg-transparent hover:text-[var(--text-primary)]"
          >
            <ArrowLeft className="h-4 w-4" />
            Back
          </Button>
        ) : null}
        <SetupPanel
          title="We found your workspace"
          subtitle={`Your ${teamDomain} email can join this Lemma workspace.`}
        >
        <div className="setup-suggestion-card mx-auto mt-9 w-full max-w-2xl px-6 py-5 text-left">
          <div className="flex items-center gap-4">
            <div className="setup-suggestion-icon flex h-12 w-12 shrink-0 items-center justify-center">
              <UsersRound className="h-6 w-6" />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-xl font-semibold text-[var(--text-primary)]">
                {suggestedOrganization.name}
              </h2>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                Matched through @{teamDomain}
              </p>
            </div>
            <span className="chip chip-pill chip-sm state-badge-success">
              <Check className="h-3.5 w-3.5" />
              Verified
            </span>
          </div>
          <div className="mt-6 grid gap-2">
            <div className="setup-info-row flex items-center gap-3 px-4 py-3 text-sm text-[var(--text-secondary)]">
              <CheckCircle2 className="h-4 w-4 text-[var(--state-success)]" />
              Your work email is eligible for this workspace.
            </div>
            <div className="setup-info-row flex items-center gap-3 px-4 py-3 text-sm text-[var(--text-secondary)]">
              <ShieldCheck className="h-4 w-4 text-[var(--text-tertiary)]" />
              You will join as a member and can see available pods after
              joining.
            </div>
          </div>
        </div>
        <SetupPrimaryButton
          onClick={onJoinSuggested}
          loading={isJoining}
          loadingLabel="Joining workspace"
        >
          Join {suggestedOrganization.name}
        </SetupPrimaryButton>
        <div className="mt-5 text-center">
          <button
            type="button"
            onClick={() => setShowManualCreate(true)}
            className="setup-secondary-action-button text-sm font-medium text-[var(--text-tertiary)] transition hover:text-[var(--text-primary)]"
          >
            Create a separate workspace
          </button>
          <p className="mx-auto mt-2 max-w-sm text-xs leading-5 text-[var(--text-soft)]">
            Use this for a different team, client workspace, or sandbox.
          </p>
        </div>
        </SetupPanel>
      </>
    );
  }

  return (
    <SetupSplitPanel
      title="Create your workspace"
      subtitle="This is where your pods, teammates, and approval rails will live."
      preview={
        <WorkspacePreviewBody
          workspaceName={workspaceName}
          allowDomainJoin={allowDomainJoin}
          domain={domain}
        />
      }
      onBack={onBack}
      currentStep="workspace"
      steps={steps}
    >
      <div className="w-full max-w-xl space-y-5">
        <div className="space-y-2">
          <Label htmlFor="workspace-name" className="block text-left">
            Workspace name
          </Label>
          <div className="form-field-control flex h-14 items-center gap-3 px-4">
            <Boxes className="h-5 w-5 text-[var(--text-tertiary)]" />
            <input
              id="workspace-name"
              value={workspaceName}
              onChange={(event) => onWorkspaceNameChange(event.target.value)}
              className="inline-edit-field min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
              placeholder="Acme Workspace"
            />
          </div>
          <p className="text-sm text-[var(--text-tertiary)]">
            {slugAvailable
              ? "This workspace URL is available."
              : "You can rename this later."}
          </p>
        </div>
        {domain ? (
          <button
            type="button"
            aria-pressed={allowDomainJoin}
            onClick={() => onAllowDomainJoinChange(!allowDomainJoin)}
            className={[
              "setup-domain-toggle flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-gentle",
              allowDomainJoin ? "is-active" : "",
            ].join(" ")}
          >
            <span
              className={[
                "setup-domain-toggle-icon flex h-8 w-8 shrink-0 items-center justify-center",
                allowDomainJoin ? "is-active" : "",
              ].join(" ")}
            >
              {allowDomainJoin ? (
                <Check className="h-4 w-4" />
              ) : (
                <ShieldCheck className="h-4 w-4" />
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-medium">
                Let teammates with @{domain} join
              </span>
              <span className="mt-0.5 block text-xs leading-5 text-[var(--text-tertiary)]">
                {allowDomainJoin
                  ? "They can enter this workspace after signing in with a matching work email."
                  : "They can request access with their work email. You approve each request."}
              </span>
            </span>
          </button>
        ) : null}
        <SetupPrimaryButton
          onClick={onCreateWorkspace}
          loading={isCreating}
          loadingLabel="Creating workspace"
          disabled={!workspaceName.trim()}
          className="!mx-0"
        >
          Create workspace
        </SetupPrimaryButton>
      </div>
    </SetupSplitPanel>
  );
}

export function IntentStep({
  intent,
  podName,
  onIntentChange,
  onIntentSelect,
  onPodNameChange,
  onDecideLater,
  onContinue,
}: {
  intent: string;
  podName: string;
  onIntentChange: (value: string) => void;
  onIntentSelect: (value: string) => void;
  onPodNameChange: (value: string) => void;
  onDecideLater: () => void;
  onContinue: () => void;
}) {
  const visibleExamples = INTENT_EXAMPLES.filter(
    (example) => example !== intent,
  ).slice(0, 3);

  return (
    <SetupPanel
      title="What should your first pod help with?"
      titleClassName="setup-title-intent"
    >
      <div className="mx-auto mt-8 w-full max-w-3xl space-y-4">
        <div className="form-field-control flex min-h-14 items-center gap-3 px-4 py-2">
          <Sparkles className="h-5 w-5 shrink-0 text-[var(--text-tertiary)]" />
          <input
            value={intent}
            onChange={(event) => {
              onIntentChange(event.target.value);
              onPodNameChange(derivePodNameFromIntent(event.target.value));
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && intent.trim() && podName.trim()) {
                onContinue();
              }
            }}
            className="inline-edit-field min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
            placeholder="Track investor follow-ups from Gmail and Slack"
          />
          <Button
            type="button"
            size="icon"
            onClick={onContinue}
            disabled={!podName.trim() || !intent.trim()}
            aria-label="Continue"
            className="setup-round-action h-9 w-9 shrink-0 disabled:pointer-events-none disabled:opacity-40"
          >
            <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2 text-sm leading-6 text-[var(--text-tertiary)]">
          <span>Try:</span>
          {visibleExamples.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => onIntentSelect(example)}
              className="setup-example-button text-[var(--text-secondary)] underline-offset-4 transition hover:text-[var(--text-primary)] hover:underline"
            >
              {INTENT_EXAMPLE_LABELS[example] || example}
            </button>
          ))}
        </div>
        <div className="mx-auto flex max-w-2xl flex-wrap items-center justify-center gap-x-2 gap-y-2 pt-5 text-sm leading-6 text-[var(--text-tertiary)] sm:pt-6">
          <label htmlFor="pod-name" className="sr-only">
            Pod name
          </label>
          <span>Pod:</span>
          <div className="setup-pod-name-pill inline-flex min-w-0 items-center gap-1.5 px-2.5 py-1 text-[var(--text-primary)]">
            <input
              id="pod-name"
              value={podName}
              onChange={(event) => onPodNameChange(event.target.value)}
              className="inline-edit-field min-w-0 max-w-[220px] border-0 bg-transparent p-0 text-center text-sm font-medium text-[var(--text-primary)] outline-none sm:max-w-[280px]"
            />
            <Pencil className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
          </div>
          <span aria-hidden="true">·</span>
          <button
            type="button"
            onClick={onDecideLater}
            className="setup-defer-button font-medium text-[var(--text-tertiary)] transition hover:text-[var(--text-primary)]"
          >
            I&apos;ll decide later
          </button>
        </div>
      </div>
    </SetupPanel>
  );
}

export function BuildPathStep({
  buildPath,
  intent,
  prompt,
  selectedKitId,
  onBuildPathChange,
  onPromptChange,
  onKitSelect,
  onContinue,
  isCreating,
}: {
  buildPath: BuildPath;
  intent: string;
  prompt: string;
  selectedKitId: string;
  onBuildPathChange: (path: BuildPath) => void;
  onPromptChange: (value: string) => void;
  onKitSelect: (kit: KitDefinition) => void;
  onContinue: () => void;
  isCreating: boolean;
}) {
  const selectedKit = getKitById(selectedKitId) || kitCatalog[0] || null;

  return (
    <SetupPanel
      title="Let's configure the pod for you"
      titleClassName="setup-title-path"
    >
      <div className="setup-path-layout mx-auto mt-7 grid w-full max-w-5xl gap-4 text-left lg:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.2fr)]">
        <div className="space-y-2">
          {BUILD_PATHS.map((path) => {
            const Icon = path.icon;
            const selected = buildPath === path.id;
            return (
              <button
                key={path.id}
                type="button"
                className={[
                  "setup-path-choice flex w-full items-center gap-3 px-3 py-3 text-left",
                  selected ? "is-active" : "",
                ].join(" ")}
                onClick={() => onBuildPathChange(path.id)}
                data-active={selected}
              >
                <span
                  className={[
                    "setup-path-choice-icon flex h-9 w-9 shrink-0 items-center justify-center",
                    selected ? "is-active" : "",
                  ].join(" ")}
                >
                  <Icon className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-[var(--text-primary)]">
                    {path.title}
                  </span>
                  <span className="mt-0.5 block text-xs leading-5 text-[var(--text-secondary)]">
                    {path.description}
                  </span>
                </span>
                {selected ? (
                  <Check className="h-4 w-4 shrink-0 text-[var(--text-primary)]" />
                ) : null}
              </button>
            );
          })}
        </div>

        <div className="setup-path-pane h-[360px] overflow-hidden p-4">
          {buildPath === "ai" ? (
            <div key="ai" className="setup-path-pane-content">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="type-eyebrow-mono">AI draft</p>
                  <h2 className="mt-1 text-base font-semibold text-[var(--text-primary)]">
                    Start from your brief
                  </h2>
                </div>
                <span className="max-w-[260px] truncate text-xs text-[var(--text-tertiary)]">
                  {intent}
                </span>
              </div>
              <Textarea
                value={prompt}
                onChange={(event) => onPromptChange(event.target.value)}
                rows={7}
                className="setup-ai-brief mt-4 resize-none p-3 text-sm leading-6 focus-visible:ring-0"
                placeholder="Tell Lemma what this pod should help with."
              />
            </div>
          ) : buildPath === "template" ? (
            <div key="template" className="setup-path-pane-content">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="type-eyebrow-mono">Kits</p>
                  <h2 className="mt-1 text-base font-semibold text-[var(--text-primary)]">
                    Choose an existing kit
                  </h2>
                </div>
                <span className="chip chip-sm font-mono">
                  {kitCatalog.length}
                </span>
              </div>
              <div className="mt-5 max-h-[250px] space-y-2 overflow-y-auto px-1 py-1">
                {kitCatalog.map((kit) => {
                  const selected = selectedKit?.id === kit.id;
                  return (
                    <button
                      key={kit.id}
                      type="button"
                      className={[
                        "setup-kit-option w-full px-3 py-3 text-left",
                        selected ? "is-active" : "",
                      ].join(" ")}
                      onClick={() => onKitSelect(kit)}
                      data-active={selected}
                    >
                      <div className="flex items-start gap-3">
                        <span className="setup-kit-icon mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center">
                          <PackageOpen className="h-4 w-4" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-semibold text-[var(--text-primary)]">
                            {kit.name}
                          </span>
                          <span className="mt-1 line-clamp-2 block text-xs leading-5 text-[var(--text-secondary)]">
                            {kit.description}
                          </span>
                          <span className="setup-kit-repo mt-2 block truncate font-mono text-[var(--text-tertiary)]">
                            {getGitHubRepoLabel(kit)}
                          </span>
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
              {!selectedKit ? (
                <p className="mt-4 text-sm text-[var(--text-tertiary)]">
                  No kits are available yet.
                </p>
              ) : null}
            </div>
          ) : (
            <div key="sdk" className="setup-path-pane-content">
              <p className="type-eyebrow-mono">SDK</p>
              <h2 className="mt-1 text-base font-semibold text-[var(--text-primary)]">
                Start locally with the CLI
              </h2>
              <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                Use this when the pod should begin as local code and resources
                you manage from a terminal.
              </p>
              <div className="setup-terminal mt-4 grid gap-2">
                {[
                  "uv tool install lemma-terminal",
                  "lemma auth login",
                  "lemma init",
                ].map((command) => (
                  <code
                    key={command}
                    className="setup-terminal-line px-3 py-2 font-mono text-xs text-[var(--text-primary)]"
                  >
                    <span className="text-[var(--text-tertiary)]">$</span>{" "}
                    {command}
                  </code>
                ))}
              </div>
            </div>
          )}
        </div>

        <Button
          type="button"
          onClick={onContinue}
          loading={isCreating}
          loadingLabel="Creating pod"
          disabled={
            isCreating ||
            (buildPath === "ai" && !prompt.trim()) ||
            (buildPath === "template" && !selectedKit)
          }
          className="setup-primary-action !flex mx-auto mt-3 h-11 min-w-44 gap-2 px-6 text-sm font-medium lg:col-span-2"
        >
          Create pod
          <ArrowRight className="h-4 w-4" />
        </Button>
      </div>
    </SetupPanel>
  );
}
