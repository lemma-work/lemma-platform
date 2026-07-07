import Image from "next/image";
import {
  Boxes,
  Check,
  KeyRound,
  Sparkles,
  Terminal,
  UsersRound,
} from "lucide-react";

import { ConfettiBurst } from "@/components/shared/resource-feedback";
import { HARNESS_LOGOS } from "@/components/agents/agent-runtime-helpers";
import { cn } from "@/lib/utils";

import { DAEMON_SETUP_STEPS, type Audience } from "./account-onboarding-helpers";

function getInitials(name: string, fallback = "?") {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return fallback;
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase() || fallback;
}

// A small, static mock of Lemma's real app chrome (top bar + sidebar + main
// area) that onboarding steps drop live-bound content into. This is the one
// visual asset every split-view step shares, so the "your workspace is
// forming" feeling is consistent from step to step rather than each step
// inventing its own preview surface.
export function OnboardingPreviewChrome({
  orgLabel,
  personName,
  sidebarItemCount = 5,
  activeSidebarItems = 1,
  children,
}: {
  orgLabel: string;
  personName?: string;
  sidebarItemCount?: number;
  activeSidebarItems?: number;
  children?: React.ReactNode;
}) {
  const initials = getInitials(personName ?? "", "");

  return (
    <div className="setup-preview-chrome">
      <div className="setup-preview-topbar">
        <div className="setup-preview-breadcrumb">
          <span className="setup-preview-breadcrumb-value">{orgLabel}</span>
        </div>
        <span className="setup-preview-avatar" aria-hidden="true">
          {initials || <UsersRound className="h-3 w-3" />}
        </span>
      </div>
      <div className="setup-preview-body">
        <div className="setup-preview-sidebar" aria-hidden="true">
          {Array.from({ length: sidebarItemCount }, (_, index) => (
            <span
              key={index}
              className={cn(
                "setup-preview-sidebar-item lemma-skeleton",
                index < activeSidebarItems ? "opacity-100" : "opacity-60",
              )}
              /* eslint-disable-next-line no-restricted-syntax -- Skeleton bar width is data-driven geometry per sidebar item. */
              style={{ width: index === 0 ? "100%" : `${72 - index * 6}%` }}
            />
          ))}
        </div>
        <div className="setup-preview-main">{children}</div>
      </div>
    </div>
  );
}

export function AudiencePreviewBody({ audience }: { audience: Audience | null }) {
  const isTeam = audience === "team";

  return (
    <OnboardingPreviewChrome
      orgLabel={isTeam ? "Acme Workspace" : "Your Space"}
      activeSidebarItems={isTeam ? 4 : 1}
      sidebarItemCount={isTeam ? 5 : 3}
    >
      {audience ? <ConfettiBurst key={audience} density="small" /> : null}
      {isTeam ? (
        <>
          <div className="setup-preview-card">
            <p className="setup-preview-card-title">Support triage</p>
            <div className="mt-2 space-y-1.5">
              <span className="setup-preview-line lemma-skeleton block w-full" />
              <span className="setup-preview-line lemma-skeleton block w-2/3" />
            </div>
          </div>
          <div className="setup-preview-card">
            <p className="setup-preview-card-title">Approvals queue</p>
            <div className="mt-2 space-y-1.5">
              <span className="setup-preview-line lemma-skeleton block w-4/5" />
            </div>
          </div>
          <span className={cn("setup-preview-badge is-visible w-fit")}>
            <UsersRound className="h-3 w-3" />
            Team workspace
          </span>
        </>
      ) : (
        <div className="setup-preview-card">
          <p className="setup-preview-card-title">My pod</p>
          <div className="mt-2 space-y-1.5">
            <span className="setup-preview-line lemma-skeleton block w-full" />
            <span className="setup-preview-line lemma-skeleton block w-1/2" />
          </div>
        </div>
      )}
    </OnboardingPreviewChrome>
  );
}

export function WorkspacePreviewBody({
  workspaceName,
  allowDomainJoin,
  domain,
}: {
  workspaceName: string;
  allowDomainJoin: boolean;
  domain: string | null;
}) {
  return (
    <OnboardingPreviewChrome
      orgLabel={workspaceName.trim() || "Your workspace"}
      activeSidebarItems={4}
    >
      <div className="setup-preview-card">
        <p className="setup-preview-card-title">
          {workspaceName.trim() || "Your workspace"}
        </p>
        <div className="mt-2 space-y-1.5">
          <span className="setup-preview-line lemma-skeleton block w-full" />
          <span className="setup-preview-line lemma-skeleton block w-2/3" />
        </div>
      </div>
      {domain ? (
        <span className={cn("setup-preview-badge w-fit", allowDomainJoin && "is-visible")}>
          <UsersRound className="h-3 w-3" />@{domain} can join
        </span>
      ) : null}
    </OnboardingPreviewChrome>
  );
}

// Canonical list of local coding-agent harnesses Lemma can drive — same set
// and titles as LOCAL_RUNTIME_SETUP_OPTIONS in agent-runtime-helpers.ts, kept
// as plain strings here so this preview doesn't need the SDK's HarnessKind
// enum just to render a status row.
const LOCAL_HARNESS_ORDER: Array<{ kind: string; title: string }> = [
  { kind: "CLAUDE_CODE", title: "Claude Code" },
  { kind: "CODEX", title: "Codex" },
  { kind: "OPENCODE", title: "OpenCode" },
  { kind: "CURSOR", title: "Cursor" },
  { kind: "ANTIGRAVITY", title: "Antigravity" },
];

export function ConnectPreviewBody({
  selectedOption,
  harnesses = [],
  selectedHarnessKind,
  providerName,
  modelName,
}: {
  selectedOption: "lemma" | "daemon" | "provider";
  harnesses?: Array<{ kind: string; detected: boolean }>;
  selectedHarnessKind?: string | null;
  providerName?: string;
  modelName?: string | null;
}) {
  if (selectedOption === "daemon") {
    const anyDetected = harnesses.some((h) => h.detected);

    return (
      <OnboardingPreviewChrome orgLabel="AI Runtime" activeSidebarItems={2}>
        <div className="setup-preview-card">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
              <Terminal className="h-3.5 w-3.5" />
            </span>
            <p className="setup-preview-card-title">Local harnesses</p>
          </div>
          <div className="mt-3 space-y-2">
            {LOCAL_HARNESS_ORDER.map(({ kind, title }) => {
              const info = harnesses.find((h) => h.kind === kind);
              const detected = info?.detected ?? false;
              const isSelected = detected && selectedHarnessKind === kind;
              const logo = HARNESS_LOGOS[kind];
              return (
                <div key={kind} className="flex items-center gap-2.5">
                  {logo ? (
                    <Image
                      src={logo}
                      alt=""
                      width={16}
                      height={16}
                      className="h-4 w-4 shrink-0 rounded-sm object-contain"
                    />
                  ) : (
                    <span className="h-4 w-4 shrink-0" />
                  )}
                  <span className="flex-1 truncate text-xs font-medium text-[var(--text-primary)]">
                    {title}
                  </span>
                  <span
                    className={cn(
                      "chip chip-sm",
                      (detected || isSelected) && "state-badge-success",
                    )}
                  >
                    {isSelected ? <Check className="h-3 w-3" /> : null}
                    {isSelected ? "Selected" : detected ? "Detected" : "Not detected"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
        {!anyDetected ? (
          <div className="setup-preview-card mt-3">
            <p className="setup-preview-card-title">Get one connected</p>
            <p className="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
              Run these from a terminal, then it appears above automatically.
            </p>
            <div className="mt-3 space-y-2.5">
              {DAEMON_SETUP_STEPS.map((step, index) => (
                <div key={step.command}>
                  <p className="text-xs font-medium text-[var(--text-tertiary)]">
                    {index + 1}. {step.label}
                  </p>
                  <code className="mt-1 block truncate rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2.5 py-1.5 font-mono text-xs leading-4 text-[var(--text-primary)]">
                    {step.command}
                  </code>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </OnboardingPreviewChrome>
    );
  }

  if (selectedOption === "provider") {
    const title = providerName?.trim() || "Your provider";
    const ready = Boolean(providerName?.trim());
    return (
      <OnboardingPreviewChrome orgLabel="AI Runtime" activeSidebarItems={2}>
        <div className="setup-preview-card">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
              <KeyRound className="h-3.5 w-3.5" />
            </span>
            <p className="setup-preview-card-title truncate">{title}</p>
          </div>
          <p className="mt-2 text-xs leading-5 text-[var(--text-tertiary)]">
            {modelName ?? "Paste an API key to connect"}
          </p>
          <span className={cn("setup-preview-badge mt-3 w-fit", ready && "is-visible")}>
            {ready ? "Ready to chat" : "Waiting for setup"}
          </span>
        </div>
      </OnboardingPreviewChrome>
    );
  }

  return (
    <OnboardingPreviewChrome orgLabel="AI Runtime" activeSidebarItems={2}>
      <div className="setup-preview-card">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
            <Sparkles className="h-3.5 w-3.5" />
          </span>
          <p className="setup-preview-card-title">Lemma</p>
        </div>
        <p className="mt-2 text-xs leading-5 text-[var(--text-tertiary)]">
          Built-in models, ready immediately — no key, no daemon.
        </p>
        <span className="setup-preview-badge is-visible mt-3 w-fit">
          Includes starter usage credits
        </span>
      </div>
    </OnboardingPreviewChrome>
  );
}

export function StartPreviewBody({
  podTitle,
  podBlurb,
  justSelected,
}: {
  podTitle: string;
  podBlurb?: string;
  justSelected: string | null;
}) {
  return (
    <OnboardingPreviewChrome orgLabel="Your workspace" activeSidebarItems={5}>
      {justSelected ? <ConfettiBurst key={justSelected} density="small" /> : null}
      <div className="setup-preview-card">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
            <Boxes className="h-3.5 w-3.5" />
          </span>
          <p className="setup-preview-card-title truncate">{podTitle}</p>
        </div>
        {podBlurb ? (
          <p className="mt-2 text-xs leading-5 text-[var(--text-tertiary)]">
            {podBlurb}
          </p>
        ) : null}
        <div className="mt-3 space-y-1.5">
          <span className="setup-preview-line lemma-skeleton block w-full" />
          <span className="setup-preview-line lemma-skeleton block w-3/4" />
          <span className="setup-preview-line lemma-skeleton block w-1/2" />
        </div>
      </div>
    </OnboardingPreviewChrome>
  );
}
