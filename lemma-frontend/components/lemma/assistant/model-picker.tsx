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
import { Check, ChevronDown, Clock, Search, Settings2, Sparkles, TerminalSquare } from "lucide-react";

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
  isCodingAgentKind,
  modelPathHint,
  runtimeCatalogToModelOptions,
  runtimeKey,
  shortModelName,
} from "@/components/agents/agent-runtime-helpers";

const AUTO_VALUE = "__AUTO_RUNTIME__";

// Recently-picked models float to the top so the daily-driver handful is one
// click away regardless of how many providers are connected. Persisted locally.
const RECENTS_KEY = "lemma:model-picker:recents";
const RECENTS_LIMIT = 6;

function loadRecentKeys(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === "string") : [];
  } catch {
    return [];
  }
}

function recordRecentKey(key: string): string[] {
  const next = [key, ...loadRecentKeys().filter((k) => k !== key)].slice(0, RECENTS_LIMIT);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
    } catch {
      // best-effort: a full or unavailable store just means no recents
    }
  }
  return next;
}

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
  /** Show the "inherit the default" (Auto) row. Off for surfaces that *set* the
   *  default — offering "use the default" there is circular. Defaults to true. */
  allowAuto?: boolean;
  /** Dialog heading. Defaults to "Choose a model". */
  title?: string;
  /** Dialog subheading — say what picking here actually sets. */
  description?: ReactNode;
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
    allowAuto = true,
    title = "Choose a model",
    description = "Pick the model for this chat.",
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
  // Recents live in localStorage; lazy-init from there, then re-read on open so a
  // pick made in another tab/composer is reflected here.
  const [recentKeys, setRecentKeys] = useState<string[]>(() => loadRecentKeys());
  // Per-group expand override. Absent → use the default (the group holding the
  // current selection is open, everything else collapsed). Reset on each open.
  const [groupOverrides, setGroupOverrides] = useState<Record<string, boolean>>({});

  const open = () => {
    setRecentKeys(loadRecentKeys());
    setGroupOverrides({});
    setIsOpen(true);
  };

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
  // With Auto hidden, an unset value has nothing to inherit — prompt a pick.
  const triggerLabel = selectedModelLabel ?? (allowAuto ? autoTriggerLabel : "Choose a model");

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
        isCodingAgent: isCodingAgentKind(harnessKind),
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

  // Map every option by its stable key so recents (stored as keys) resolve back
  // to live options — stale entries for removed providers simply drop out.
  const optionByKey = useMemo(() => {
    const map = new Map<string, AvailableModelInfo>();
    options.forEach((option) => map.set(optionKeyFor(option), option));
    return map;
  }, [options]);

  const recentOptions = useMemo(
    () => recentKeys.map((key) => optionByKey.get(key)).filter((o): o is AvailableModelInfo => Boolean(o)),
    [recentKeys, optionByKey],
  );

  const close = () => {
    setIsOpen(false);
    setQuery("");
  };

  const handleSelect = (
    nextValue: string | null,
    nextRuntime: AgentRuntimeConfig | null,
    recordKey?: string,
  ) => {
    if (recordKey) setRecentKeys(recordRecentKey(recordKey));
    onChange(nextValue, nextRuntime);
    close();
  };

  const searching = Boolean(query.trim());

  // A group is open when searching (so matches show), or per an explicit toggle,
  // else by default only if it holds the current selection.
  const isGroupExpanded = (group: ProviderGroup) =>
    searching || (groupOverrides[group.key] ?? group.options.some((o) => optionKeyFor(o) === selectedKey));

  const toggleGroup = (key: string, currentlyExpanded: boolean) => {
    setGroupOverrides((prev) => ({ ...prev, [key]: !currentlyExpanded }));
  };

  return (
    <div ref={ref} className={className} {...props}>
      <button
        type="button"
        onClick={open}
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
          if (nextOpen) {
            setRecentKeys(loadRecentKeys());
            setGroupOverrides({});
          }
          setIsOpen(nextOpen);
          if (!nextOpen) setQuery("");
        }}
      >
        <DialogContent className="flex max-h-[min(620px,calc(100vh-40px))] w-[min(640px,calc(100vw-32px))] max-w-none grid-rows-none flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="shrink-0 border-b border-[var(--border-subtle)] px-5 py-4 pr-12">
            <DialogTitle className="text-xl">{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
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
            {allowAuto ? (
              <ModelChoiceRow
                icon={<Sparkles className="size-4 text-[var(--action-primary)]" />}
                title={typeof autoLabel === "string" ? autoLabel : "Auto"}
                subtitle={autoSubtitle}
                selected={isAuto}
                onClick={() => handleSelect(null, null)}
              />
            ) : null}

            {!query.trim() && recentOptions.length > 0 ? (
              <div className="mt-4">
                <ProviderHeader icon={<Clock className="size-3.5" />} label="Recently used" />
                {recentOptions.map((option) => {
                  const optionRuntime = modelRuntime(option);
                  const modelName = optionRuntime?.model_name ?? option.name ?? option.id;
                  const key = optionKeyFor(option);
                  return (
                    <ModelChoiceRow
                      key={`recent-${key}`}
                      title={shortModelName(modelName)}
                      subtitle={option.profile?.name ?? option.description ?? modelPathHint(modelName)}
                      selected={selectedKey === key}
                      onClick={() => handleSelect(option.id, optionRuntime, key)}
                    />
                  );
                })}
              </div>
            ) : null}

            {modelGroups.length > 0 ? (
              <div className="mt-4 flex flex-col gap-2">
                {modelGroups.map((group) => (
                  <ProviderCard
                    key={group.key}
                    icon={<Sparkles className="size-4 text-[var(--text-tertiary)]" />}
                    label={group.displayName}
                    count={group.options.length}
                    expanded={isGroupExpanded(group)}
                    hasSelection={group.options.some((o) => optionKeyFor(o) === selectedKey)}
                    onToggle={() => toggleGroup(group.key, isGroupExpanded(group))}
                  >
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
                          onClick={() => handleSelect(option.id, optionRuntime, key)}
                        />
                      );
                    })}
                  </ProviderCard>
                ))}
              </div>
            ) : null}

            {codingGroups.length > 0 ? (
              <div className="mt-5">
                <ProviderHeader icon={<TerminalSquare className="size-3.5" />} label="Local agents" />
                <div className="mt-1 flex flex-col gap-2">
                  {codingGroups.map((group) => {
                    const logo = group.harnessKind ? HARNESS_LOGOS[group.harnessKind] : undefined;
                    return (
                      <ProviderCard
                        key={group.key}
                        icon={
                          logo ? (
                            <Image src={logo} alt="" width={16} height={16} className="size-4 object-contain" />
                          ) : (
                            <TerminalSquare className="size-4 text-[var(--text-tertiary)]" />
                          )
                        }
                        label={group.displayName}
                        count={group.options.length}
                        expanded={isGroupExpanded(group)}
                        hasSelection={group.options.some((o) => optionKeyFor(o) === selectedKey)}
                        onToggle={() => toggleGroup(group.key, isGroupExpanded(group))}
                      >
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
                              onClick={() => handleSelect(option.id, optionRuntime, key)}
                            />
                          );
                        })}
                      </ProviderCard>
                    );
                  })}
                </div>
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
  /** Dialog heading. Defaults to "Choose a model". */
  title?: string;
  /** Dialog subheading — say what picking here actually sets. */
  description?: ReactNode;
  /** Label for the "inherit the default" row. Defaults to "Default". */
  autoLabel?: ReactNode;
  /** Subtitle for that row. Defaults to what the default currently resolves to. */
  autoSubtitle?: ReactNode;
  /** Show the "inherit the default" (Auto) row. Off for surfaces that set the
   *  default themselves. Defaults to true. */
  allowAuto?: boolean;
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
  title,
  description,
  autoLabel,
  autoSubtitle,
  allowAuto,
}: RuntimeModelPickerProps) {
  const options = useMemo(
    () => runtimeCatalogToModelOptions(catalog, availableHarnesses),
    [catalog, availableHarnesses],
  );
  const defaultModelLabel = defaultRuntime?.model_name ? shortModelName(defaultRuntime.model_name) : undefined;
  // "Currently <model>" signals this tracks the default — it'll move if the
  // default changes, unlike pinning a specific model below. Callers that *are*
  // the default (e.g. the pod-default setting) override this, since "use the
  // default" is circular there.
  const resolvedAutoSubtitle = autoSubtitle ?? (defaultModelLabel ? `Currently ${defaultModelLabel}` : "Use the pod default");

  return (
    <ModelPicker
      className={className}
      value={value?.model_name ?? null}
      runtime={value ?? null}
      options={options}
      onChange={(_, runtime) => onChange(runtime ?? null)}
      autoLabel={autoLabel}
      autoSubtitle={resolvedAutoSubtitle}
      autoModelLabel={defaultModelLabel}
      allowAuto={allowAuto}
      scopeHint={scopeHint}
      manageHref={manageHref}
      disabled={disabled}
      compact={compact}
      title={title}
      description={description}
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

// A provider/agent rendered as a collapsible card: header shows the name and
// model count, the body (its models) is hidden until expanded. Auto-collapsed by
// default; the card holding the current selection opens itself (see isGroupExpanded).
function ProviderCard({
  icon,
  label,
  count,
  expanded,
  hasSelection,
  onToggle,
  children,
}: {
  icon: ReactNode;
  label: string;
  count: number;
  expanded: boolean;
  hasSelection: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border transition-colors",
        hasSelection ? "border-[var(--border-strong)]" : "border-[var(--border-subtle)]",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-[var(--surface-2)]"
      >
        <span className="flex size-6 shrink-0 items-center justify-center">{icon}</span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">{label}</span>
        {hasSelection && !expanded ? (
          <span className="size-1.5 shrink-0 rounded-full bg-[var(--action-primary)]" />
        ) : null}
        <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
          {count} {count === 1 ? "model" : "models"}
        </span>
        <ChevronDown
          className={cn("size-4 shrink-0 text-[var(--text-tertiary)] transition-transform", expanded && "rotate-180")}
        />
      </button>
      {expanded ? <div className="border-t border-[var(--border-subtle)] p-1">{children}</div> : null}
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
