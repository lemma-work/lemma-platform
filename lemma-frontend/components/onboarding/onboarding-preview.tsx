import {
  Bot,
  BookOpen,
  Boxes,
  Bug,
  Check,
  Database,
  FolderOpen,
  Handshake,
  KeyRound,
  LifeBuoy,
  Mail,
  PanelsTopLeft,
  Plug,
  Receipt,
  Sparkles,
  UsersRound,
  Workflow,
  type LemmaIcon,
} from "@/components/ui/icons";

import { ConfettiBurst } from "@/components/shared/resource-feedback";
import { cn } from "@/lib/utils";

import { type Audience } from "./account-onboarding-helpers";

function getInitials(name: string, fallback = "?") {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return fallback;
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase() || fallback;
}

// Real section names from the pod sidebar (components/pod/workspace-sidebar.tsx)
// — the mock nav shows the actual vocabulary a pod will have instead of
// abstract placeholder bars that mean nothing.
const PREVIEW_NAV_ITEMS: Array<{ label: string; icon: LemmaIcon }> = [
  { label: "Apps", icon: PanelsTopLeft },
  { label: "Agents", icon: Bot },
  { label: "Workflows", icon: Workflow },
  { label: "Data", icon: Database },
  { label: "Docs", icon: FolderOpen },
  { label: "Connectors", icon: Plug },
];

// Concrete, everyday examples — no "orchestration" or "automation platform"
// talk. Each one should be something a person can picture happening to them.
//
// Team and personal are drawn with deliberately different shapes, not just
// different copy: a team gets one pod each (three separate cards below), but
// a person mostly keeps adding to a single pod over time — like a Notion
// workspace, not a folder per project — so personal renders as one card with
// items accumulating inside it instead of three standalone cards.
const TEAM_EXAMPLE_PODS: Array<{ name: string; icon: LemmaIcon; blurb: string }> = [
  { name: "Support", icon: LifeBuoy, blurb: "Answers the common questions, hands you the rest." },
  { name: "Sales", icon: Handshake, blurb: "Drafts a follow-up after every call." },
  { name: "Engineering", icon: Bug, blurb: "Turns a bug report in Slack into a tracked ticket." },
];

// Deliberately a mix of kinds (agent, agent, app) — the point is that a
// single pod holds different building blocks, not just a list of bots.
const PERSONAL_EXAMPLE_ITEMS: Array<{
  name: string;
  icon: LemmaIcon;
  kind: string;
  blurb: string;
}> = [
  { name: "Inbox triage", icon: Mail, kind: "Agent", blurb: "Sums up this morning's emails in three lines." },
  { name: "Expenses", icon: Receipt, kind: "App", blurb: "Logs a receipt the moment you text a photo." },
  { name: "Reading list", icon: BookOpen, kind: "Agent", blurb: "Saves what you send it, recaps it Sunday night." },
];

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
          {PREVIEW_NAV_ITEMS.slice(0, sidebarItemCount).map(
            ({ label, icon: Icon }, index) => (
              <span
                key={label}
                className={cn(
                  "setup-preview-sidebar-item",
                  index < activeSidebarItems ? "is-active" : "",
                )}
              >
                <Icon className="h-3 w-3 shrink-0" />
                {label}
              </span>
            ),
          )}
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
      activeSidebarItems={isTeam ? 5 : 1}
      sidebarItemCount={isTeam ? 6 : 4}
    >
      {audience ? <ConfettiBurst key={audience} density="small" /> : null}
      {isTeam ? (
        <>
          <p className="text-xs leading-5 text-[var(--text-tertiary)]">
            One workspace, everyone in it — but each team gets its own pod:
          </p>
          <div className="mt-2.5 space-y-2">
            {TEAM_EXAMPLE_PODS.map(({ name, icon: Icon, blurb }) => (
              <div key={name} className="setup-preview-card !py-2.5">
                <div className="flex items-center gap-2">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)] text-[var(--text-secondary)]">
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                  <p className="setup-preview-card-title text-xs">{name}</p>
                </div>
                <p className="mt-1 pl-8 text-xs leading-5 text-[var(--text-tertiary)]">
                  {blurb}
                </p>
              </div>
            ))}
          </div>
          <span className={cn("setup-preview-badge is-visible mt-1 w-fit")}>
            <UsersRound className="h-3 w-3" />
            Support, Sales, and Engineering never see each other&apos;s pods
          </span>
        </>
      ) : (
        <>
          <p className="text-xs leading-5 text-[var(--text-tertiary)]">
            Most people keep everything in one pod, adding to it as they go:
          </p>
          <div className="setup-preview-card mt-2.5">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)] text-[var(--text-secondary)]">
                <Boxes className="h-3.5 w-3.5" />
              </span>
              <p className="setup-preview-card-title text-xs">Your Space</p>
            </div>
            <div className="mt-2.5 space-y-2 pl-1">
              {PERSONAL_EXAMPLE_ITEMS.map(({ name, icon: Icon, kind, blurb }) => (
                <div key={name} className="flex items-start gap-2">
                  <Icon className="mt-0.5 h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
                  <p className="min-w-0 flex-1 text-xs leading-5 text-[var(--text-secondary)]">
                    <span className="font-medium text-[var(--text-primary)]">{name}</span>{" "}
                    <span className="setup-preview-kind-tag">{kind}</span>
                    {" — "}
                    {blurb}
                  </p>
                </div>
              ))}
            </div>
          </div>
          <p className="mt-2.5 text-xs leading-5 text-[var(--text-tertiary)]">
            Same pod, same data — your inbox agent can drop a receipt straight
            into the expenses app the moment it spots one. A totally different
            part of your life still gets its own pod.
          </p>
        </>
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
        <p className="mt-1.5 text-xs leading-5 text-[var(--text-tertiary)]">
          Where your pods and teammates will live.
        </p>
      </div>
      {domain ? (
        <span className={cn("setup-preview-badge w-fit", allowDomainJoin && "is-visible")}>
          <UsersRound className="h-3 w-3" />@{domain} can join
        </span>
      ) : null}
    </OnboardingPreviewChrome>
  );
}

export function ConnectPreviewBody({
  selectedOption,
  providerName,
  modelName,
}: {
  selectedOption: "lemma" | "provider";
  providerName?: string;
  modelName?: string | null;
}) {
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
          Built-in models, ready immediately — no key or machine setup.
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
        <p className="mt-2 text-xs leading-5 text-[var(--text-tertiary)]">
          {podBlurb ?? "Lemma wires up the bots and apps this needs."}
        </p>
      </div>
    </OnboardingPreviewChrome>
  );
}
