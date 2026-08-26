"use client";

import Image from "next/image";
import {
  forwardRef,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import type {
  AgentRuntimeConfig,
  AgentRuntimeProfileListResponse,
  AvailableModelInfo,
} from "lemma-sdk";
import { Check, ChevronDown, ChevronLeft, ChevronRight, Settings2 } from "@/components/ui/icons";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  harnessLogo,
  hydrateRuntimeModel,
  isLocalAgentKind,
  profileHarnessKey,
  resolveRuntimeModelName,
  runtimeCatalogToModelOptions,
  runtimeKey,
  humanizeModelName,
  shortModelName,
} from "@/components/agents/agent-runtime-helpers";

const AUTO_VALUE = "__AUTO_RUNTIME__";

// Recently-picked models float to the top so the daily-driver handful is one
// click away regardless of how many providers are connected. Persisted locally.
const RECENTS_KEY = "lemma:model-picker:recents";
const RECENTS_LIMIT = 6;

// How many models the popover offers before you have to ask for the rest.
// Past about five rows a list stops being something you take in at a glance and
// becomes something you scan — and the number keys, which are the point of the
// short list, run out.
const QUICK_LIMIT = 5;

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
  /** Which coding agent this group runs, when it is one — e.g. "claude-code". */
  harnessKey: string | null;
  displayName: string;
  isCodingAgent: boolean;
  options: AvailableModelInfo[];
}

/** Which of the two things the popover is showing. */
type PickerView = "quick" | "all";

/**
 * Every word must appear somewhere. That is the whole rule.
 *
 * cmdk scores by subsequence, which reads a query as one ordered run: searching
 * "claude code opus" finds nothing in a haystack that says "opus … Claude Code",
 * purely because the words arrive in the other order — and provider-then-model
 * is exactly how people type. Fuzzy scoring earns its keep on prose; model names
 * are dashes and version digits, where it mostly manufactures near-misses.
 */
function filterModels(value: string, search: string): number {
  const haystack = value.toLowerCase();
  const terms = search.toLowerCase().split(/\s+/).filter(Boolean);
  return terms.every((term) => haystack.includes(term)) ? 1 : 0;
}

function modelRuntime(option: AvailableModelInfo): AgentRuntimeConfig | null {
  if (option.runtime) return option.runtime;
  if (option.profile_id) return { profile_id: option.profile_id, model_name: option.id };
  return null;
}

function optionModelName(option: AvailableModelInfo): string {
  return modelRuntime(option)?.model_name ?? option.name ?? option.id;
}

/**
 * The model as a person reads it, and the same string the trigger prints.
 *
 * The rows used to show the raw slug and hang the humanised name off the
 * trigger, which was survivable while every row carried the full path as a
 * subtitle. On one line it just read as two names for one model.
 *
 * A catalog that ships a display name has already made this decision, and made
 * it better than a slug with its dashes beaten into spaces can be. Humanise
 * only what arrives as an identifier.
 */
function optionLabel(option: AvailableModelInfo): string {
  const modelName = optionModelName(option);
  if (option.name && option.name !== modelName) return option.name;
  return humanizeModelName(modelName);
}

function providerName(harnessKey?: string | null): string {
  if (!harnessKey) return "Models";
  return harnessKey
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function groupName(option: AvailableModelInfo): string {
  return option.agentRuntime?.name
    ?? option.profile?.name
    ?? providerName(profileHarnessKey(option.profile));
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
  /** Accessible name for the trigger — say which model this one sets. */
  ariaLabel?: string;
  /** Label for the inherit-the-default choice. */
  autoLabel?: ReactNode;
  /** Short name of the model Auto resolves to. Shown dim beside the Auto row,
   *  and on the trigger when Auto is selected. */
  autoModelLabel?: string;
  /** Optional compact trigger text when Auto is selected. The row still uses autoLabel. */
  autoTriggerLabel?: ReactNode;
  /** Footer hint, e.g. "Just for this chat" or "Default for this agent". */
  scopeHint?: ReactNode;
  /** Where "Manage models" links — connect providers, set up coding agents. */
  manageHref?: string;
  compact?: boolean;
  /** Optional classes for the trigger button and its visible label. */
  triggerClassName?: string;
  triggerLabelClassName?: string;
  onChange: (value: string | null, runtime?: AgentRuntimeConfig | null) => void;
}

/**
 * The daily-use model chooser: pick a model, nothing else.
 *
 * A popover, not a dialog, and that is the whole design. Switching model is
 * cheap, reversible and usually decided *mid-draft* — a modal dims the composer
 * you were writing in and takes the sentence you were about to send with it.
 * It also sits on one line with the agent, project and branch chips, all three
 * of which are popovers; a dialog made the middle one behave like a different
 * class of control.
 *
 * Two views on one surface. The short list is what you came for: the current
 * default, the handful of models actually used here, one line each, hit by
 * number key. The full catalog — every provider, every local agent, searchable —
 * is one row away and replaces the contents in place rather than escalating to
 * a second window. Provider management (BYO keys, installing coding agents)
 * still lives in settings, behind "Manage models".
 *
 * Presentational — the caller owns the available options and state.
 */
export const ModelPicker = forwardRef<HTMLDivElement, ModelPickerProps>(function ModelPicker(
  {
    value,
    runtime,
    options,
    disabled,
    allowAuto = true,
    ariaLabel = "Model",
    autoLabel = "Default",
    autoModelLabel,
    autoTriggerLabel,
    scopeHint = "Just for this chat",
    manageHref,
    compact = false,
    triggerClassName,
    triggerLabelClassName,
    onChange,
    className,
    ...props
  },
  ref,
) {
  const [isOpen, setIsOpen] = useState(false);
  const [view, setView] = useState<PickerView>("quick");
  // Recents live in localStorage; lazy-init from there, then re-read on open so a
  // pick made in another tab/composer is reflected here.
  const [recentKeys, setRecentKeys] = useState<string[]>(() => loadRecentKeys());
  // Arrow keys move real DOM focus between the short list's rows, so the handler
  // needs to find them.
  const quickListRef = useRef<HTMLDivElement | null>(null);

  const selectedRuntime = useMemo<AgentRuntimeConfig | null>(() => {
    if (runtime) return runtime;
    if (!value) return null;
    const match = options.find((option) => option.id === value);
    return match ? modelRuntime(match) : null;
  }, [options, runtime, value]);

  const selectedKey = selectedRuntime ? runtimeKey(selectedRuntime) : value ?? AUTO_VALUE;

  // Map every option by its stable key so recents (stored as keys) resolve back
  // to live options — stale entries for removed providers simply drop out.
  const optionByKey = useMemo(() => {
    const map = new Map<string, AvailableModelInfo>();
    options.forEach((option) => map.set(optionKeyFor(option), option));
    return map;
  }, [options]);
  const isAuto = !value && !runtime;

  // A pinned profile the catalog no longer offers — archived, or gone with the
  // computer it ran on. `selectedRuntime` short-circuits on the prop, so without
  // this the trigger keeps printing its stale model name confidently while no
  // row inside is checked. An empty `options` means still loading.
  const selectionIsMissing = useMemo(() => {
    const profileId = selectedRuntime?.profile_id;
    if (!profileId || options.length === 0) return false;
    return !options.some(
      (option) => (modelRuntime(option)?.profile_id ?? option.profile_id) === profileId,
    );
  }, [options, selectedRuntime]);

  // Name the selection exactly as its row does, catalog display name included —
  // the trigger and the row are two views of one choice, and a person who
  // picked "Claude Haiku 4.5" should not find "Claude haiku 4 5" on the chip.
  const selectedOption = optionByKey.get(selectedKey);
  const selectedModelLabel = selectedOption
    ? optionLabel(selectedOption)
    : selectedRuntime?.model_name
      ? humanizeModelName(selectedRuntime.model_name)
      : value
        ? humanizeModelName(value)
        : null;
  // On an explicit pick, show the model. On Auto, show what it resolves to —
  // so a configured default is visible without opening the picker.
  const resolvedAutoTriggerLabel = autoTriggerLabel ?? autoModelLabel ?? autoLabel;
  // With Auto hidden, an unset value has nothing to inherit — prompt a pick.
  const triggerLabel = selectionIsMissing
    ? "Model unavailable"
    : selectedModelLabel ?? (allowAuto ? resolvedAutoTriggerLabel : "Choose a model");

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
        harnessKey: profileHarnessKey(option.profile),
        displayName: groupName(option),
        isCodingAgent: isLocalAgentKind(harnessKind),
        options: [option],
      });
    });
    const all = Array.from(byKey.values()).sort((a, b) => a.displayName.localeCompare(b.displayName));
    // Hosted providers first, local agents after: a coding agent on your own
    // machine is the deliberate choice, not the one you land on by scrolling.
    return [...all.filter((g) => !g.isCodingAgent), ...all.filter((g) => g.isCodingAgent)];
  }, [options]);

  const recentOptions = useMemo(
    () => recentKeys.map((key) => optionByKey.get(key)).filter((o): o is AvailableModelInfo => Boolean(o)),
    [recentKeys, optionByKey],
  );

  // What the popover offers without being asked. The current pick leads, because
  // an agent or pod default can point at a model this browser has never chosen
  // and it must still be visible and checked. Then what has actually been used
  // here, then whatever else exists so a fresh install is not an empty list.
  const quickOptions = useMemo(() => {
    const picked: AvailableModelInfo[] = [];
    const seen = new Set<string>();
    const push = (option?: AvailableModelInfo) => {
      if (!option) return;
      const key = optionKeyFor(option);
      if (seen.has(key)) return;
      seen.add(key);
      picked.push(option);
    };
    if (!isAuto) push(optionByKey.get(selectedKey));
    recentOptions.forEach(push);
    options.forEach(push);
    return picked.slice(0, QUICK_LIMIT);
  }, [isAuto, optionByKey, options, recentOptions, selectedKey]);

  // Name the provider only when the model name alone is ambiguous. Two rows both
  // reading "Opus 5" — one hosted, one through a coding agent on your laptop —
  // are not the same choice, and everywhere else the provider is noise.
  const quickMetaByKey = useMemo(() => {
    const counts = new Map<string, number>();
    quickOptions.forEach((option) => {
      const name = optionLabel(option);
      counts.set(name, (counts.get(name) ?? 0) + 1);
    });
    const map = new Map<string, string>();
    quickOptions.forEach((option) => {
      if ((counts.get(optionLabel(option)) ?? 0) < 2) return;
      map.set(optionKeyFor(option), groupName(option));
    });
    return map;
  }, [quickOptions]);

  // cmdk scores the search against each item's `value`, so the value carries the
  // whole haystack: display name, full path, provider, harness. Uniqueness is a
  // cmdk requirement rather than a display concern — two identically named
  // profiles offering the same model would otherwise highlight as one row — so
  // collisions get a counter rather than the profile UUID, which would match
  // hex-shaped queries and quietly poison the results.
  const searchValueByKey = useMemo(() => {
    const used = new Set<string>();
    const map = new Map<string, string>();
    groups.forEach((group) => {
      group.options.forEach((option) => {
        const modelName = optionModelName(option);
        const base = [shortModelName(modelName), modelName, group.displayName, group.harnessKey]
          .filter(Boolean)
          .join(" ");
        let candidate = base;
        let suffix = 2;
        while (used.has(candidate)) candidate = `${base} ${suffix++}`;
        used.add(candidate);
        map.set(optionKeyFor(option), candidate);
      });
    });
    return map;
  }, [groups]);

  const hasMore = options.length > quickOptions.length;

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setRecentKeys(loadRecentKeys());
      setView("quick");
    }
    setIsOpen(nextOpen);
  };

  const handleSelect = (
    nextValue: string | null,
    nextRuntime: AgentRuntimeConfig | null,
    recordKey?: string,
  ) => {
    if (recordKey) setRecentKeys(recordRecentKey(recordKey));
    onChange(nextValue, nextRuntime);
    setIsOpen(false);
  };

  const selectOption = (option: AvailableModelInfo) => {
    handleSelect(option.id, modelRuntime(option), optionKeyFor(option));
  };

  // The short list has no text input, which is what makes the number keys
  // possible: 1–5 pick a row outright. The full list does have one, so every key
  // there belongs to cmdk — including the digits, which are part of model names.
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (view !== "quick" || event.metaKey || event.ctrlKey || event.altKey) return;

    if (/^[1-9]$/.test(event.key)) {
      const option = quickOptions[Number(event.key) - 1];
      if (!option) return;
      event.preventDefault();
      selectOption(option);
      return;
    }

    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const rows = Array.from(
      quickListRef.current?.querySelectorAll<HTMLButtonElement>("[data-model-row]") ?? [],
    );
    if (rows.length === 0) return;
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    const current = rows.indexOf(document.activeElement as HTMLButtonElement);
    const next = current === -1
      ? (step === 1 ? 0 : rows.length - 1)
      : (current + step + rows.length) % rows.length;
    rows[next]?.focus();
  };

  return (
    <div ref={ref} className={className} {...props}>
      <Popover open={isOpen} onOpenChange={handleOpenChange}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            className={cn(
              "lemma-assistant-runtime-trigger-button inline-flex max-w-[240px] items-center rounded-lg border border-[var(--row-border)] bg-[var(--field-bg)] text-left text-sm font-medium shadow-none transition-colors hover:border-[var(--field-border-hover)] disabled:cursor-not-allowed disabled:opacity-55",
              compact ? "h-8 min-w-0 gap-1.5 px-2" : "h-9 min-w-28 gap-2 px-2.5",
              triggerClassName,
            )}
            aria-label={ariaLabel}
          >
            <span
              className={cn(
                "rounded-full border border-[var(--chip-border)] bg-[var(--chip-bg)] px-1.5 py-0.5 text-xs font-medium text-[var(--text-secondary)]",
                compact && "sr-only",
              )}
            >
              Model
            </span>
            <span className={cn("min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]", triggerLabelClassName)}>
              {triggerLabel}
            </span>
            <ChevronDown className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
          </button>
        </PopoverTrigger>

        <PopoverContent align="start" className="w-[19rem] overflow-hidden p-0" onKeyDown={handleKeyDown}>
          {view === "quick" ? (
            <>
              <div ref={quickListRef} className="p-1">
                {allowAuto ? (
                  <QuickRow
                    label={typeof autoLabel === "string" ? autoLabel : "Auto"}
                    meta={autoModelLabel}
                    selected={isAuto}
                    onSelect={() => handleSelect(null, null)}
                  />
                ) : null}
                {allowAuto && quickOptions.length > 0 ? (
                  <div className="my-1 h-px bg-[var(--border-subtle)]" />
                ) : null}
                {quickOptions.map((option, index) => {
                  const key = optionKeyFor(option);
                  return (
                    <QuickRow
                      key={key}
                      label={optionLabel(option)}
                      meta={quickMetaByKey.get(key)}
                      shortcut={String(index + 1)}
                      selected={selectedKey === key}
                      onSelect={() => selectOption(option)}
                    />
                  );
                })}
                {quickOptions.length === 0 ? (
                  <p className="px-2 py-3 text-xs text-[var(--text-tertiary)]">
                    No other models yet. Connect a provider or a local agent to choose one.
                  </p>
                ) : null}
              </div>

              {hasMore ? (
                <div className="border-t border-[var(--border-subtle)] p-1">
                  <button
                    type="button"
                    onClick={() => setView("all")}
                    className="lemma-assistant-runtime-group-button flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none"
                  >
                    <span className="min-w-0 flex-1 truncate">More models…</span>
                    <ChevronRight className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <Command filter={filterModels} className="h-auto rounded-none border-0 bg-transparent shadow-none">
              <div className="flex items-center border-b border-[var(--border-subtle)] bg-[var(--surface-2)] [&_[cmdk-input-wrapper]]:min-w-0 [&_[cmdk-input-wrapper]]:flex-1 [&_[cmdk-input-wrapper]]:border-0 [&_[cmdk-input-wrapper]]:bg-transparent [&_[cmdk-input-wrapper]]:pl-0 [&_[cmdk-input-wrapper]_svg]:hidden">
                {/* The back arrow takes the search icon's slot rather than
                    sitting beside it — two glyphs before the placeholder read as
                    decoration, and only one of them is a control. */}
                <Button
                  type="button"
                  variant="quiet"
                  size="icon"
                  onClick={() => setView("quick")}
                  aria-label="Back to recent models"
                  className="size-8 shrink-0 rounded-md text-[var(--text-tertiary)]"
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <CommandInput placeholder="Search models" className="h-9" autoFocus />
              </div>
              <CommandList className="max-h-[17rem]">
                <CommandEmpty className="px-2 py-6 text-center text-sm text-[var(--text-tertiary)]">
                  No models match that.
                </CommandEmpty>
                {groups.map((group) => (
                  <CommandGroup key={group.key} heading={<GroupHeading group={group} />}>
                    {group.options.map((option) => {
                      const key = optionKeyFor(option);
                      return (
                        <CommandItem
                          key={key}
                          value={searchValueByKey.get(key) ?? key}
                          onSelect={() => selectOption(option)}
                        >
                          <span className="min-w-0 flex-1 truncate">
                            {optionLabel(option)}
                          </span>
                          {option.description ? (
                            <span
                              className="max-w-[7rem] shrink-0 truncate text-xs text-[var(--text-tertiary)]"
                              title={option.description}
                            >
                              {option.description}
                            </span>
                          ) : null}
                          <Check
                            className={cn(
                              "size-3.5 shrink-0",
                              selectedKey === key ? "text-[var(--action-primary)]" : "text-transparent",
                            )}
                          />
                        </CommandItem>
                      );
                    })}
                  </CommandGroup>
                ))}
              </CommandList>
            </Command>
          )}

          {manageHref || scopeHint ? (
            <div className="flex items-center justify-between gap-2 border-t border-[var(--border-subtle)] px-3 py-2 text-xs text-[var(--text-tertiary)]">
              {manageHref ? (
                <a
                  href={manageHref}
                  className="inline-flex items-center gap-1.5 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  <Settings2 className="size-3.5" />
                  Manage models
                </a>
              ) : <span />}
              {scopeHint ? <span className="min-w-0 truncate">{scopeHint}</span> : null}
            </div>
          ) : null}
        </PopoverContent>
      </Popover>
    </div>
  );
});

export interface RuntimeModelPickerProps {
  catalog?: AgentRuntimeProfileListResponse;
  /** The default the "Auto" choice falls back to — named beside the Auto row. */
  defaultRuntime?: AgentRuntimeConfig | null;
  /** Current selection. null = inherit the default (Auto). */
  value?: AgentRuntimeConfig | null;
  onChange: (runtime: AgentRuntimeConfig | null) => void;
  disabled?: boolean;
  compact?: boolean;
  scopeHint?: ReactNode;
  manageHref?: string;
  className?: string;
  triggerClassName?: string;
  triggerLabelClassName?: string;
  /** Accessible name for the trigger. Defaults to "Model". */
  ariaLabel?: string;
  /** Label for the "inherit the default" row. Defaults to "Default". */
  autoLabel?: ReactNode;
  /** Optional compact trigger text when the inherited default is selected. */
  autoTriggerLabel?: ReactNode;
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
  defaultRuntime,
  value,
  onChange,
  disabled,
  compact,
  scopeHint,
  manageHref,
  className,
  triggerClassName,
  triggerLabelClassName,
  ariaLabel,
  autoLabel,
  autoTriggerLabel,
  allowAuto,
}: RuntimeModelPickerProps) {
  const options = useMemo(() => runtimeCatalogToModelOptions(catalog), [catalog]);
  // A stored selection can pin a profile and leave the model open — a bundle,
  // the CLI and the API all accept `{ profile_id }` on its own. Name that model
  // the way the backend picks it, or the trigger falls through to the *default's*
  // label and an explicitly pinned agent reads as one inheriting a model it does
  // not run, with no row checked inside the picker to contradict it.
  const selected = useMemo(() => hydrateRuntimeModel(value ?? null, catalog), [value, catalog]);
  // The default usually pins only a profile, so ask the catalog which model that
  // profile will actually run rather than showing a nameless "Default". Printed
  // dim beside the Auto row, where it reads as what the default resolves to
  // today — this choice tracks the default and will move with it, unlike pinning
  // one of the models below.
  const defaultModelName = resolveRuntimeModelName(defaultRuntime, catalog);
  const defaultModelLabel = defaultModelName ? humanizeModelName(defaultModelName) : undefined;

  return (
    <ModelPicker
      className={className}
      triggerClassName={triggerClassName}
      triggerLabelClassName={triggerLabelClassName}
      value={selected?.model_name ?? null}
      runtime={selected}
      options={options}
      onChange={(_, runtime) => onChange(runtime ?? null)}
      ariaLabel={ariaLabel}
      autoLabel={autoLabel}
      autoModelLabel={defaultModelLabel}
      autoTriggerLabel={autoTriggerLabel}
      allowAuto={allowAuto}
      scopeHint={scopeHint}
      manageHref={manageHref}
      disabled={disabled}
      compact={compact}
    />
  );
}

function GroupHeading({ group }: { group: ProviderGroup }) {
  const logo = group.isCodingAgent ? harnessLogo(group.harnessKey) : undefined;
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      {logo ? (
        <Image src={logo} alt="" width={12} height={12} className="size-3 shrink-0 object-contain" />
      ) : null}
      <span className="truncate">{group.displayName}</span>
    </span>
  );
}

/**
 * One line: the model, optionally who runs it, a check, a number key.
 *
 * Deliberately not the two-line row this used to be. The subtitle was the title
 * again in slug form on most models — "Opus 5" over "claude-opus-5" — and it
 * doubled the height of the one list people open several times a day.
 */
function QuickRow({
  label,
  meta,
  shortcut,
  selected,
  onSelect,
}: {
  label: string;
  meta?: string;
  shortcut?: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-model-row=""
      onClick={onSelect}
      className={cn(
        "model-picker-choice-button flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-sm transition-colors hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none",
        selected ? "text-[var(--text-primary)]" : "text-[var(--text-secondary)]",
      )}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {meta ? (
        <span className="max-w-[7rem] shrink-0 truncate text-xs text-[var(--text-tertiary)]">{meta}</span>
      ) : null}
      <Check
        className={cn("size-3.5 shrink-0", selected ? "text-[var(--action-primary)]" : "text-transparent")}
      />
      <span className="w-2 shrink-0 text-right text-xs text-[var(--text-tertiary)]">{shortcut ?? ""}</span>
    </button>
  );
}
