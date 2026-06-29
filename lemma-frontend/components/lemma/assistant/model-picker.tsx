"use client";

import Image from "next/image";
import {
  forwardRef,
  useMemo,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import type {
  AgentHarnessListResponse,
  AgentRuntimeConfig,
  AgentRuntimeProfileListResponse,
  AvailableModelInfo,
} from "lemma-sdk";
import { Check, ChevronDown, Search, Settings2, Sparkles, TerminalSquare } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  HARNESS_LOGOS,
  modelPathHint,
  runtimeCatalogToModelOptions,
  runtimeKey,
  shortModelName,
} from "@/components/agents/agent-runtime-helpers";

const AUTO_VALUE = "__AUTO_RUNTIME__";

// The harness kinds we present as "coding agents" — local terminal agents that
// run a model, as opposed to plain model providers. Anything else (Lemma's
// built-in models, a BYO OpenAI/Anthropic key) is a model provider.
const CODING_AGENT_KINDS = new Set(["CLAUDE_CODE", "CODEX", "OPENCODE", "ANTIGRAVITY"]);

interface ProviderGroup {
  key: string;
  harnessKind: string | null;
  displayName: string;
  isCodingAgent: boolean;
  options: AvailableModelInfo[];
}

function modelRuntime(option: AvailableModelInfo): AgentRuntimeConfig | null {
  if (option.runtime) return option.runtime;
  if (option.profile_id) return { profile_id: option.profile_id, model_name: option.id };
  return null;
}

function providerName(harnessKind?: string | null): string {
  if (!harnessKind) return "Models";
  return harnessKind
    .split("_")
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function optionKeyFor(option: AvailableModelInfo): string {
  const runtime = modelRuntime(option);
  return runtime ? runtimeKey(runtime) : option.id;
}

export interface ModelPickerProps extends Omit<ComponentPropsWithoutRef<"div">, "onChange"> {
  value: string | null;
  runtime?: AgentRuntimeConfig | null;
  options: AvailableModelInfo[];
  disabled?: boolean;
  /** Label for the inherit-the-default choice. */
  autoLabel?: ReactNode;
  /** What "Auto" resolves to right now, shown under the Auto row. */
  autoSubtitle?: ReactNode;
  /** Short name of the model Auto resolves to — shown on the trigger as "Auto · <model>". */
  autoModelLabel?: string;
  /** Footer hint, e.g. "Just for this chat" or "Default for this agent". */
  scopeHint?: ReactNode;
  /** Where "Manage models" links — connect providers, set up coding agents. */
  manageHref?: string;
  compact?: boolean;
  onChange: (value: string | null, runtime?: AgentRuntimeConfig | null) => void;
}

/**
 * The lightweight, daily-use model chooser: pick a model, nothing else.
 * Provider management (BYO keys, installing coding agents) lives in settings,
 * not here. Presentational — the caller owns the available options and state.
 */
export const ModelPicker = forwardRef<HTMLDivElement, ModelPickerProps>(function ModelPicker(
  {
    value,
    runtime,
    options,
    disabled,
    autoLabel = "Default",
    autoSubtitle = "Use the workspace default",
    autoModelLabel,
    scopeHint = "Just for this chat",
    manageHref,
    compact = false,
    onChange,
    className,
    ...props
  },
  ref,
) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");

  const selectedRuntime = useMemo<AgentRuntimeConfig | null>(() => {
    if (runtime) return runtime;
    if (!value) return null;
    const match = options.find((option) => option.id === value);
    return match ? modelRuntime(match) : null;
  }, [options, runtime, value]);

  const selectedKey = selectedRuntime ? runtimeKey(selectedRuntime) : value ?? AUTO_VALUE;
  const isAuto = !value && !runtime;

  const selectedModelLabel = selectedRuntime?.model_name
    ? shortModelName(selectedRuntime.model_name)
    : value
      ? shortModelName(value)
      : null;
  // On an explicit pick, show the model. On Auto, show what it resolves to —
  // "Auto · <model>" — so a configured default is visible without opening the picker.
  const autoTriggerLabel = autoModelLabel
    ? `${typeof autoLabel === "string" ? autoLabel : "Auto"} · ${autoModelLabel}`
    : autoLabel;
  const triggerLabel = selectedModelLabel ?? autoTriggerLabel;

  const groups = useMemo<ProviderGroup[]>(() => {
    const byKey = new Map<string, ProviderGroup>();
    options.forEach((option) => {
      const optionRuntime = modelRuntime(option);
      const harnessKind = option.harness_kind ?? null;
      const key = optionRuntime?.profile_id ?? option.profile_id ?? harnessKind ?? "MODELS";
      const existing = byKey.get(key);
      if (existing) {
        existing.options.push(option);
        return;
      }
      byKey.set(key, {
        key,
        harnessKind,
        displayName: option.agentRuntime?.name ?? option.profile?.name ?? providerName(harnessKind),
        isCodingAgent: harnessKind ? CODING_AGENT_KINDS.has(harnessKind) : false,
        options: [option],
      });
    });
    return Array.from(byKey.values()).sort((a, b) => a.displayName.localeCompare(b.displayName));
  }, [options]);

  const filtered = useMemo<ProviderGroup[]>(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((group) => {
        const groupMatches = `${group.displayName} ${group.harnessKind ?? ""}`.toLowerCase().includes(q);
        if (groupMatches) return group;
        const matchingOptions = group.options.filter((option) => {
          const optionRuntime = modelRuntime(option);
          const haystack = [
            option.id,
            option.name,
            optionRuntime?.model_name,
            shortModelName(optionRuntime?.model_name ?? option.name ?? option.id),
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          return haystack.includes(q);
        });
        if (matchingOptions.length === 0) return null;
        return { ...group, options: matchingOptions };
      })
      .filter((group): group is ProviderGroup => Boolean(group));
  }, [groups, query]);

  const modelGroups = filtered.filter((group) => !group.isCodingAgent);
  const codingGroups = filtered.filter((group) => group.isCodingAgent);

  const close = () => {
    setIsOpen(false);
    setQuery("");
  };

  const handleSelect = (nextValue: string | null, nextRuntime: AgentRuntimeConfig | null) => {
    onChange(nextValue, nextRuntime);
    close();
  };

  return (
    <div ref={ref} className={className} {...props}>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        disabled={disabled}
        className={cn(
          "lemma-assistant-runtime-trigger-button inline-flex max-w-[240px] items-center rounded-lg border border-[var(--row-border)] bg-[var(--field-bg)] text-left text-sm font-medium shadow-none transition-colors hover:border-[var(--field-border-hover)] disabled:cursor-not-allowed disabled:opacity-55",
          compact ? "h-8 min-w-0 gap-1.5 px-2" : "h-9 min-w-28 gap-2 px-2.5",
        )}
        aria-label="Conversation model"
      >
        <span
          className={cn(
            "rounded-full border border-[var(--chip-border)] bg-[var(--chip-bg)] px-1.5 py-0.5 text-xs font-semibold text-[var(--text-secondary)]",
            compact && "sr-only",
          )}
        >
          Model
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--text-primary)]">
          {triggerLabel}
        </span>
        <ChevronDown className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
      </button>

      <Dialog
        open={isOpen}
        onOpenChange={(nextOpen) => {
          setIsOpen(nextOpen);
          if (!nextOpen) setQuery("");
        }}
      >
        <DialogContent className="flex max-h-[min(620px,calc(100vh-40px))] w-[min(640px,calc(100vw-32px))] max-w-none grid-rows-none flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="shrink-0 border-b border-[var(--border-subtle)] px-5 py-4 pr-12">
            <DialogTitle className="text-xl">Choose a model</DialogTitle>
            <DialogDescription>Pick the model for this chat.</DialogDescription>
          </DialogHeader>

          <div className="shrink-0 border-b border-[var(--border-subtle)] px-5 py-3">
            <div className="flex h-10 items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--field-bg)] px-3 focus-within:border-[var(--field-border-focus)]">
              <Search className="size-4 shrink-0 text-[var(--text-tertiary)]" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search models"
                className="inline-edit-field h-full min-w-0 flex-1 bg-transparent text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <ModelChoiceRow
              icon={<Sparkles className="size-4 text-[var(--action-primary)]" />}
              title={typeof autoLabel === "string" ? autoLabel : "Auto"}
              subtitle={autoSubtitle}
              selected={isAuto}
              onClick={() => handleSelect(null, null)}
            />

            {modelGroups.map((group) => (
              <div key={group.key} className="mt-4 first:mt-3">
                <ProviderHeader
                  icon={<Sparkles className="size-3.5" />}
                  label={group.displayName}
                />
                {group.options.map((option) => {
                  const optionRuntime = modelRuntime(option);
                  const modelName = optionRuntime?.model_name ?? option.name ?? option.id;
                  const key = optionKeyFor(option);
                  return (
                    <ModelChoiceRow
                      key={key}
                      title={shortModelName(modelName)}
                      subtitle={option.description ?? modelPathHint(modelName)}
                      selected={selectedKey === key}
                      onClick={() => handleSelect(option.id, optionRuntime)}
                    />
                  );
                })}
              </div>
            ))}

            {codingGroups.length > 0 ? (
              <div className="mt-5">
                <ProviderHeader
                  icon={<TerminalSquare className="size-3.5" />}
                  label="Local agents"
                />
                {codingGroups.map((group) => {
                  const logo = group.harnessKind ? HARNESS_LOGOS[group.harnessKind] : undefined;
                  return (
                    <div key={group.key} className="mt-1">
                      <div className="flex items-center gap-2 px-3 pb-1 pt-2">
                        {logo ? (
                          <Image src={logo} alt="" width={16} height={16} className="size-4 object-contain" />
                        ) : (
                          <TerminalSquare className="size-4 text-[var(--text-tertiary)]" />
                        )}
                        <span className="text-xs font-medium text-[var(--text-secondary)]">{group.displayName}</span>
                      </div>
                      {group.options.map((option) => {
                        const optionRuntime = modelRuntime(option);
                        const modelName = optionRuntime?.model_name ?? option.name ?? option.id;
                        const key = optionKeyFor(option);
                        return (
                          <ModelChoiceRow
                            key={key}
                            title={shortModelName(modelName)}
                            subtitle={option.description ?? modelPathHint(modelName)}
                            selected={selectedKey === key}
                            onClick={() => handleSelect(option.id, optionRuntime)}
                          />
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            ) : null}

            {modelGroups.length === 0 && codingGroups.length === 0 ? (
              query.trim() ? (
                <div className="mt-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-3 text-sm text-[var(--text-secondary)]">
                  No models match “{query.trim()}”.
                </div>
              ) : (
                <div className="mt-6 px-3 pb-2 text-center">
                  <p className="text-sm text-[var(--text-secondary)]">No other models yet</p>
                  <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                    Connect a provider or local agent to choose a specific model.
                  </p>
                </div>
              )
            ) : null}
          </div>

          {scopeHint || manageHref ? (
            <div className="flex shrink-0 items-center justify-between border-t border-[var(--border-subtle)] px-5 py-3 text-xs text-[var(--text-tertiary)]">
              {manageHref ? (
                <a
                  href={manageHref}
                  className="inline-flex items-center gap-1.5 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  <Settings2 className="size-3.5" />
                  Manage models
                </a>
              ) : <span />}
              {scopeHint ? <span>{scopeHint}</span> : null}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
});

export interface RuntimeModelPickerProps {
  catalog?: AgentRuntimeProfileListResponse;
  availableHarnesses?: AgentHarnessListResponse;
  /** The default the "Auto" choice falls back to — shown under the Auto row. */
  defaultRuntime?: AgentRuntimeConfig | null;
  /** Current selection. null = inherit the default (Auto). */
  value?: AgentRuntimeConfig | null;
  onChange: (runtime: AgentRuntimeConfig | null) => void;
  disabled?: boolean;
  compact?: boolean;
  scopeHint?: ReactNode;
  manageHref?: string;
  className?: string;
}

/**
 * Catalog-driven adapter over ModelPicker: the single picker for every surface
 * that needs to *choose* a runtime (chat composer, agent editor, pod default).
 * Takes the runtime-profile catalog, flattens it to model options, and reports
 * the selection as `AgentRuntimeConfig | null`. Provider and local-agent setup
 * lives on the Models settings page, reachable via `manageHref` — not here.
 */
export function RuntimeModelPicker({
  catalog,
  availableHarnesses,
  defaultRuntime,
  value,
  onChange,
  disabled,
  compact,
  scopeHint,
  manageHref,
  className,
}: RuntimeModelPickerProps) {
  const options = useMemo(
    () => runtimeCatalogToModelOptions(catalog, availableHarnesses),
    [catalog, availableHarnesses],
  );
  const defaultModelLabel = defaultRuntime?.model_name ? shortModelName(defaultRuntime.model_name) : undefined;
  // "Currently <model>" signals this tracks the default — it'll move if the
  // default changes, unlike pinning a specific model below.
  const autoSubtitle = defaultModelLabel ? `Currently ${defaultModelLabel}` : "Use the pod default";

  return (
    <ModelPicker
      className={className}
      value={value?.model_name ?? null}
      runtime={value ?? null}
      options={options}
      onChange={(_, runtime) => onChange(runtime ?? null)}
      autoSubtitle={autoSubtitle}
      autoModelLabel={defaultModelLabel}
      scopeHint={scopeHint}
      manageHref={manageHref}
      disabled={disabled}
      compact={compact}
    />
  );
}

function ProviderHeader({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5 px-3 pb-1 text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">
      <span className="text-[var(--text-tertiary)]">{icon}</span>
      {label}
    </div>
  );
}

function ModelChoiceRow({
  icon,
  title,
  subtitle,
  selected,
  onClick,
}: {
  icon?: ReactNode;
  title: string;
  subtitle?: ReactNode;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "model-picker-choice-button flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors hover:bg-[var(--surface-2)]",
        selected && "bg-[var(--action-primary-soft)]",
      )}
    >
      {icon ? <span className="flex size-5 shrink-0 items-center justify-center">{icon}</span> : null}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium leading-5 text-[var(--text-primary)]">{title}</span>
        {subtitle ? (
          <span className="block truncate text-xs leading-4 text-[var(--text-tertiary)]">{subtitle}</span>
        ) : null}
      </span>
      <span
        className={cn(
          "flex size-[18px] shrink-0 items-center justify-center rounded-full border",
          selected
            ? "border-[var(--action-primary)] bg-[var(--action-primary)] text-[var(--text-on-brand)]"
            : "border-[var(--border-subtle)] text-transparent",
        )}
      >
        <Check className="size-3.5" />
      </span>
    </button>
  );
}
