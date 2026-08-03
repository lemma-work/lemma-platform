import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  Building2,
  Check,
  CheckCircle2,
  Code2,
  Copy,
  KeyRound,
  PackageOpen,
  Pencil,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from "@/components/ui/icons";

import { WaitingScreen } from "@/components/shared/loading";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import {
  CUSTOM_PROVIDER_OPTIONS,
  splitModelNames,
  type CustomProviderKind,
} from "@/components/agents/agent-runtime-helpers";

import {
  SetupPanel,
  SetupPrimaryButton,
  SetupShell,
  SetupSplitPanel,
  SetupStandalonePage,
} from "./account-onboarding-chrome";
import {
  AudiencePreviewBody,
  ConnectPreviewBody,
  StartPreviewBody,
  WorkspacePreviewBody,
} from "./onboarding-preview";
import {
  AUDIENCE_OPTIONS,
  BUILD_PATHS,
  INTENT_EXAMPLE_LABELS,
  INTENT_EXAMPLES,
  SETUP_GREETINGS,
  TEAM_OPTIONS,
  derivePodNameFromIntent,
  codingAgentStarterPrompt,
  podNameForAudience,
  splitGraphemes,
  teamLabelForKind,
  type Audience,
  type CodingAgentKind,
  type BuildPath,
  type ConnectChoice,
  type OnboardingStartDetails,
  type OnboardingStartPath,
  type SetupStep,
  type TeamKind,
} from "./account-onboarding-helpers";
import { StepLoader } from "@/components/brand/loader";

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
      <WaitingScreen
        title="Joining your workspace"
        description="Accepting your invitation so setup can wait until later."
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
      <Button variant="primary"
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
  domain,
  workspaceName,
  organization,
  organizationAction,
  isResolvingWorkspace,
  isSaving,
  onNameChange,
  onSubmit,
  onBack,
}: {
  email: string;
  name: string;
  domain: string | null;
  workspaceName: string;
  organization: Organization | null;
  organizationAction: "continue" | "join" | "create";
  isResolvingWorkspace: boolean;
  isSaving: boolean;
  onNameChange: (value: string) => void;
  onSubmit: (event: React.FormEvent) => void;
  onBack?: () => void;
  steps?: SetupStep[];
}) {
  return (
    <SetupStandalonePage
      onBack={onBack}
      meta={email ? <span className="hidden sm:inline">Signed in as {email}</span> : null}
    >
      <div className="m-auto w-full max-w-lg pb-10">
        <p className="setup-first-run-eyebrow">Welcome to Lemma</p>
        <h1 className="mt-3 font-display text-2xl font-medium leading-tight tracking-tight text-[var(--text-primary)] sm:text-3xl">
          What should we call you?
        </h1>
        <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">
          Confirm your name and where your first Lemma workspace should live.
        </p>

        <form onSubmit={onSubmit} className="mt-7 text-left">
          <div className="space-y-2">
            <Label htmlFor="operator-name" className="text-sm text-[var(--text-secondary)]">
              Your name
            </Label>
            <Input
              id="operator-name"
              value={name}
              onChange={(event) => onNameChange(event.target.value)}
              className="setup-identity-field h-12 w-full px-3.5 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
              placeholder="Ada Lovelace"
              autoComplete="name"
              autoFocus
              required
            />
          </div>

          <div className="mt-6 border-t border-[color:var(--border-subtle)] pt-5">
            <p className="text-sm font-medium text-[var(--text-secondary)]">
              Organization
            </p>
            {isResolvingWorkspace ? (
              <div className="setup-organization-destination mt-2.5 flex items-center gap-2.5 px-3 py-2.5">
                <StepLoader size="sm" className="text-[var(--text-tertiary)]" />
                <span className="text-xs text-[var(--text-secondary)]">
                  Checking organizations for your email…
                </span>
              </div>
            ) : (
              <div className="setup-organization-destination mt-2.5 flex items-start gap-2.5 px-3 py-2.5">
                <span className="setup-organization-destination-icon flex h-8 w-8 shrink-0 items-center justify-center">
                  {organizationAction === "create" ? (
                    <Sparkles className="h-4 w-4" />
                  ) : (
                    <Building2 className="h-4 w-4" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-[var(--text-primary)]">
                    {organizationAction === "create"
                      ? `Creating ${workspaceName}`
                      : organizationAction === "join"
                        ? `Joining ${organization?.name}`
                        : `Using ${organization?.name}`}
                  </span>
                  <span className="mt-0.5 block text-xs leading-5 text-[var(--text-tertiary)]">
                    {organizationAction === "join"
                      ? `${domain ? `Your @${domain} email` : "Your email"} gives you access.`
                      : organizationAction === "create" && domain
                        ? `Anyone with an @${domain} email can join.`
                        : organizationAction === "create"
                          ? "Private by default. Rename it or invite people later."
                          : "Your first workspace will be created here."}
                  </span>
                </span>
              </div>
            )}
          </div>

          <Button variant="primary"
            type="submit"
            loading={isSaving}
            loadingLabel={
              organizationAction === "join"
                ? "Joining organization"
                : "Creating organization"
            }
            disabled={isResolvingWorkspace}
            className="setup-primary-action !flex mt-5 h-11 w-full gap-2 text-sm font-medium"
          >
            Continue
            <ArrowRight className="h-4 w-4" />
          </Button>
        </form>
      </div>
    </SetupStandalonePage>
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
                    <StepLoader size="sm" />
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
  const [selectedOption, setSelectedOption] = useState<"lemma" | "provider">(
    "lemma",
  );
  // Hovering a card previews it on the right without expanding its form —
  // clicking still does that (and selects it) separately.
  const [hoveredOption, setHoveredOption] = useState<
    "lemma" | "provider" | null
  >(null);

  const [providerKind, setKindKind] = useState<CustomProviderKind>("openai");
  const [providerName, setKindName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelNames, setModelNames] = useState("");
  const [defaultModelName, setDefaultModelName] = useState("");

  const handleContinue = () => {
    if (selectedOption === "lemma") {
      onContinue({ kind: "lemma" });
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

  const providerCanContinue =
    selectedOption !== "provider" ||
    (Boolean(providerName.trim()) &&
      Boolean(apiKey.trim()) &&
      (providerKind === "anthropic" || Boolean(baseUrl.trim())));
  const continueDisabled = isSaving || !providerCanContinue;

  const previewOption = hoveredOption ?? selectedOption;
  const previewModelName =
    previewOption === "provider"
      ? defaultModelName.trim() || splitModelNames(modelNames)[0] || null
      : null;

  return (
    <SetupSplitPanel
      title="Connect your AI"
      subtitle="Choose how Lemma runs AI for you. You can change this anytime in settings."
      preview={
        <ConnectPreviewBody
          selectedOption={previewOption}
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
          selected={selectedOption === "provider"}
          onClick={() => {
            setSelectedOption("provider");
            const preset = PROVIDER_PRESETS.find((p) => p.id !== "custom");
            if (!providerName && preset) setKindName(preset.name);
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
                        setKindKind(preset.providerKind);
                        setKindName(preset.name);
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
                    setKindKind(option.kind);
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
                onChange={(e) => setKindName(e.target.value)}
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

        <Button variant="primary"
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

const START_PATHS: Array<{
  id: Exclude<OnboardingStartPath, "coding-agents" | "templates">;
  title: string;
  description: string;
  tone: "channel" | "tools" | "app" | "coding";
}> = [
  {
    id: "telegram",
    title: "Build a Telegram agent + app",
    description:
      "Give it custom instructions. Let people message it, while a companion app keeps the work organized.",
    tone: "channel",
  },
  {
    id: "chatgpt",
    title: "Use Lemma from ChatGPT or Claude",
    description:
      "Expose a live Lemma workspace through MCP, so your AI can read, update, and continue the work.",
    tone: "tools",
  },
  {
    id: "internal-app",
    title: "Build an internal AI app",
    description:
      "A real app for your team, with agents working behind it.",
    tone: "app",
  },
  {
    id: "agent-skin",
    title: "Build a skin for your local agents",
    description:
      "Put a persistent workspace, live state, controls, and history around Codex, Claude Code, or OpenCode.",
    tone: "coding",
  },
];

const SUPPORT_PATHS: Array<{
  id: Extract<OnboardingStartPath, "coding-agents" | "templates">;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    id: "coding-agents",
    title: "Build with your coding agents",
    description: "Get a prompt for Codex, Claude Code, or OpenCode",
    icon: Code2,
  },
  {
    id: "templates",
    title: "Explore templates",
    description: "Start from a complete, proven shape",
    icon: Boxes,
  },
];

const START_PATH_IMAGES: Record<
  (typeof START_PATHS)[number]["id"],
  { src: string; alt: string }
> = {
  telegram: {
    src: "/onboarding/start-paths/telegram-agent-app.png",
    alt: "A Telegram conversation saving a product idea into a companion Logbook app",
  },
  chatgpt: {
    src: "/onboarding/start-paths/external-ai-mcp.png",
    alt: "A ChatGPT or Claude conversation updating a live Lemma pipeline through MCP",
  },
  "internal-app": {
    src: "/onboarding/start-paths/internal-ai-app.png",
    alt: "An internal partner review app with a research agent preparing decisions",
  },
  "agent-skin": {
    src: "/onboarding/start-paths/local-agent-skin.png",
    alt: "A local coding agent connected to a persistent Lemma workspace with controls and history",
  },
};

function StartPathIllustration({
  path,
}: {
  path: (typeof START_PATHS)[number]["id"];
}) {
  const image = START_PATH_IMAGES[path];

  return (
    <div className="setup-route-illustration">
      <Image
        src={image.src}
        alt={image.alt}
        fill
        sizes="(min-width: 640px) 500px, calc(100vw - 32px)"
        loading="eager"
        className="setup-route-illustration-image"
      />
    </div>
  );
}

export function StartStep({
  isCreating,
  onChoosePath,
  onBack,
  initialPath,
  initialBrief = "",
}: {
  isCreating: boolean;
  onChoosePath: (
    path: OnboardingStartPath,
    details: OnboardingStartDetails,
  ) => Promise<boolean>;
  onBack?: () => void;
  initialPath?: Exclude<OnboardingStartPath, "templates">;
  initialBrief?: string;
  steps?: SetupStep[];
}) {
  const [selectedPath, setSelectedPath] =
    useState<Exclude<OnboardingStartPath, "templates"> | null>(
      initialPath ?? null,
    );
  const [pendingPath, setPendingPath] = useState<OnboardingStartPath | null>(null);
  const [brief, setBrief] = useState(initialBrief);
  const [secondaryBrief, setSecondaryBrief] = useState("");
  const [codingAgent, setCodingAgent] = useState<CodingAgentKind>("codex");
  const [promptCopied, setPromptCopied] = useState(false);

  const choosePath = async (
    path: OnboardingStartPath,
    details: OnboardingStartDetails,
  ) => {
    if (isCreating || pendingPath) return;
    setPendingPath(path);
    try {
      const isNavigating = await onChoosePath(path, details);
      if (isNavigating) return;
      setPendingPath(null);
    } catch {
      setPendingPath(null);
    }
  };

  if (selectedPath) {
    if (selectedPath === "coding-agents") {
      const starterPrompt = codingAgentStarterPrompt(codingAgent);

      return (
        <SetupStandalonePage
          onBack={() => {
            setSelectedPath(null);
            setPromptCopied(false);
          }}
        >
          <div className="m-auto w-full max-w-2xl pb-12">
            <p className="setup-first-run-eyebrow">Coding agent pathway</p>
            <h1 className="mt-3 font-display text-2xl font-medium leading-tight tracking-tight text-[var(--text-primary)] sm:text-3xl">
              Start from your coding agent
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">
              Open your project in Codex, Claude Code, or OpenCode and paste
              this prompt. The agent will inspect the repository and build the
              Lemma side from there.
            </p>

            <div className="mt-7">
              <Label className="text-sm text-[var(--text-secondary)]">
                Coding agent
              </Label>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {[
                  { id: "codex" as const, label: "Codex" },
                  { id: "claude-code" as const, label: "Claude Code" },
                  { id: "opencode" as const, label: "OpenCode" },
                ].map((agent) => (
                  <Button
                    key={agent.id}
                    type="button"
                    variant="quiet"
                    data-active={codingAgent === agent.id}
                    onClick={() => {
                      setCodingAgent(agent.id);
                      setPromptCopied(false);
                    }}
                    className="setup-detail-choice h-11 justify-between px-3 text-sm"
                  >
                    {agent.label}
                    {codingAgent === agent.id ? (
                      <Check className="h-4 w-4 text-[var(--action-primary)]" />
                    ) : null}
                  </Button>
                ))}
              </div>
            </div>

            <div className="mt-5">
              <Label
                htmlFor="coding-agent-prompt"
                className="text-sm text-[var(--text-secondary)]"
              >
                Paste this from the root of your project
              </Label>
              <Textarea
                id="coding-agent-prompt"
                readOnly
                rows={12}
                value={starterPrompt}
                className="setup-code-prompt mt-2 resize-none font-mono text-xs leading-5"
              />
            </div>

            <Button variant="quiet"
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(starterPrompt).then(() => {
                  setPromptCopied(true);
                  toast.success("Prompt copied");
                });
              }}
              className="setup-primary-action !flex mt-5 h-11 w-full gap-2 text-sm font-medium"
            >
              {promptCopied ? (
                <Check className="h-4 w-4" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
              {promptCopied ? "Prompt copied" : `Copy for ${
                codingAgent === "claude-code"
                  ? "Claude Code"
                  : codingAgent === "opencode"
                    ? "OpenCode"
                    : "Codex"
              }`}
            </Button>
          </div>
        </SetupStandalonePage>
      );
    }

    const detailCopy = {
      telegram: {
        eyebrow: "Telegram agent + app",
        title: "What should your agent do?",
        description:
          "Write the instructions it should operate by. Lemma will turn them into the agent's custom instructions, then build the companion app around the resulting work.",
        label: "Agent instructions",
        placeholder:
          "Example: Capture every voice note and message, classify it as an idea, task, or person, and ask one follow-up question when context is missing.",
        secondaryLabel: "What should the companion app keep organized?",
        secondaryPlaceholder:
          "Example: A searchable logbook with people, ideas, tasks, and weekly review.",
        action: "Build agent + app",
      },
      chatgpt: {
        eyebrow: "External AI + Lemma MCP",
        title: "What work should your AI keep updated?",
        description:
          "Lemma will create the durable work state. ChatGPT or Claude can read it, update it, and continue where they left off through the pod's MCP surface.",
        label: "Work state",
        placeholder:
          "Example: Keep my fundraising pipeline current—investors, last interaction, open questions, next step, and anything that is going stale.",
        secondaryLabel: null,
        secondaryPlaceholder: null,
        action: "Create shared work state",
      },
      "internal-app": {
        eyebrow: "Internal AI app",
        title: "What should the app help your team do?",
        description:
          "Start with one repeated job or decision. Lemma will build the app in front, with agents and durable state behind it.",
        label: "The job to be done",
        placeholder:
          "Example: Review inbound partnership requests, enrich each company, recommend an owner, and prepare an approve-or-reject decision.",
        secondaryLabel: "Who will use it?",
        secondaryPlaceholder:
          "Example: Partnerships and operations",
        action: "Build internal app",
      },
      "agent-skin": {
        eyebrow: "Local agent workspace",
        title: "What should stay visible around your agent?",
        description:
          "The local agent remains the executor. Lemma becomes the persistent product surface around it—state, controls, outputs, and history that survive each terminal session.",
        label: "Persistent workspace",
        placeholder:
          "Example: The current plan, task queue, run status, decisions, generated artifacts, and everything waiting for my review.",
        secondaryLabel: "How should people steer it?",
        secondaryPlaceholder:
          "Example: Approve the plan, reprioritize tasks, retry a failed step, and review generated work.",
        action: "Build agent workspace",
      },
    }[selectedPath];

    return (
      <SetupStandalonePage
        onBack={() => {
          if (pendingPath) return;
          setSelectedPath(null);
          setBrief("");
          setSecondaryBrief("");
        }}
      >
        <div className="m-auto w-full max-w-2xl pb-12">
          <p className="setup-first-run-eyebrow">{detailCopy.eyebrow}</p>
          <h1 className="mt-3 font-display text-2xl font-medium leading-tight tracking-tight text-[var(--text-primary)] sm:text-3xl">
            {detailCopy.title}
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">
            {detailCopy.description}
          </p>

          {selectedPath === "agent-skin" ? (
            <div className="mt-7">
              <Label className="text-sm text-[var(--text-secondary)]">
                Local agent
              </Label>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {[
                  { id: "codex" as const, label: "Codex" },
                  { id: "claude-code" as const, label: "Claude Code" },
                  { id: "opencode" as const, label: "OpenCode" },
                ].map((agent) => (
                  <Button
                    key={agent.id}
                    type="button"
                    variant="quiet"
                    data-active={codingAgent === agent.id}
                    onClick={() => setCodingAgent(agent.id)}
                    className="setup-detail-choice h-11 justify-between px-3 text-sm"
                  >
                    {agent.label}
                    {codingAgent === agent.id ? (
                      <Check className="h-4 w-4 text-[var(--action-primary)]" />
                    ) : null}
                  </Button>
                ))}
              </div>
            </div>
          ) : null}

          <div className="mt-7">
            <Label
              htmlFor="start-path-brief"
              className="text-sm text-[var(--text-secondary)]"
            >
              {detailCopy.label}
            </Label>
            <Textarea
              id="start-path-brief"
              autoFocus
              rows={6}
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              placeholder={detailCopy.placeholder}
              className="setup-detail-textarea mt-2 resize-none text-sm leading-6"
            />
          </div>

          {detailCopy.secondaryLabel ? (
            <div className="mt-5">
              <Label
                htmlFor="start-path-secondary"
                className="text-sm text-[var(--text-secondary)]"
              >
                {detailCopy.secondaryLabel}
                <span className="ml-1 font-normal text-[var(--text-tertiary)]">
                  optional
                </span>
              </Label>
              <Input
                id="start-path-secondary"
                value={secondaryBrief}
                onChange={(event) => setSecondaryBrief(event.target.value)}
                placeholder={detailCopy.secondaryPlaceholder || undefined}
                className="setup-detail-input mt-2 h-11 text-sm"
              />
            </div>
          ) : null}

          {selectedPath === "chatgpt" ? (
            <div className="setup-mcp-note mt-5 flex items-start gap-3 px-4 py-3">
              <span className="setup-mcp-pill mt-0.5">MCP</span>
              <p className="text-xs leading-5 text-[var(--text-secondary)]">
                The pod remains the source of truth. Connecting an external AI
                gives it scoped tools to work with that state—it does not copy
                the work into a disposable chat.
              </p>
            </div>
          ) : null}

          <Button variant="primary"
            type="button"
            onClick={() =>
              void choosePath(selectedPath, {
                brief,
                secondaryBrief,
                codingAgent,
              })
            }
            loading={pendingPath === selectedPath}
            loadingLabel="Preparing your pod"
            disabled={!brief.trim() || isCreating || Boolean(pendingPath)}
            className="setup-primary-action !flex mt-6 h-11 w-full gap-2 text-sm font-medium"
          >
            {detailCopy.action}
            <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </SetupStandalonePage>
    );
  }

  return (
    <SetupStandalonePage onBack={onBack}>
      <div className="mx-auto w-full max-w-6xl pb-6 pt-2">
        <div className="mx-auto max-w-2xl text-center">
          <p className="setup-first-run-eyebrow">Create with Lemma</p>
          <h1 className="mt-2 font-display text-2xl font-medium leading-tight tracking-tight text-[var(--text-primary)] sm:text-3xl">
            What do you want to make?
          </h1>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">
            Start from the way people will use it. Each path sets up a different
            kind of pod.
          </p>
        </div>

        <div className="mx-auto mt-7 grid max-w-5xl gap-3 sm:grid-cols-2">
          {START_PATHS.map((path) => {
            return (
              <Button
                key={path.id}
                type="button"
                variant="quiet"
                onClick={() => setSelectedPath(path.id)}
                disabled={isCreating}
                data-tone={path.tone}
                className="setup-route-card group h-auto min-h-64 flex-col items-stretch justify-start whitespace-normal p-0 text-left"
              >
                <StartPathIllustration path={path.id} />
                <span className="flex items-start gap-4 px-5 pb-5 pt-4">
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-[var(--text-primary)]">
                      {path.title}
                    </span>
                    <span className="mt-1 block max-w-md text-xs leading-5 text-[var(--text-secondary)]">
                      {path.description}
                    </span>
                  </span>
                  <span className="setup-route-arrow mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full">
                    <ArrowRight className="h-3.5 w-3.5" />
                  </span>
                </span>
              </Button>
            );
          })}
        </div>

        <div className="mx-auto mt-4 grid max-w-3xl gap-2 sm:grid-cols-2">
          {SUPPORT_PATHS.map((path) => {
            const Icon = path.icon;
            const isPending = pendingPath === path.id;
            return (
              <Button
                key={path.id}
                type="button"
                variant="quiet"
                onClick={() => {
                  if (path.id === "coding-agents") {
                    setSelectedPath(path.id);
                    return;
                  }
                  void choosePath("templates", { brief: "" });
                }}
                disabled={isCreating || Boolean(pendingPath)}
                className="setup-support-path h-auto justify-start gap-3 whitespace-normal px-4 py-3.5 text-left"
              >
                <span className="setup-support-path-icon flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                  {isPending ? (
                    <StepLoader size="sm" />
                  ) : (
                    <Icon className="h-4 w-4" />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-semibold text-[var(--text-primary)]">
                    {path.title}
                  </span>
                  <span className="mt-0.5 block text-xs text-[var(--text-tertiary)]">
                    {path.description}
                  </span>
                </span>
              </Button>
            );
          })}
        </div>
      </div>
    </SetupStandalonePage>
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
            variant="quiet"
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
          <Button variant="primary"
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

        <Button variant="primary"
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
