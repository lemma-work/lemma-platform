"use client";

// Pure presentational helpers extracted from assistant-experience.tsx: class-name
// builders, runtime-label formatting, the markdown component map + default message
// renderer, suggestion-card parsing, the default pending-file chip, and the
// @-mention matcher. These return strings/JSX from explicit inputs (no hooks, no
// component-local state), so AssistantExperienceView consumes them unchanged.

import { type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentRuntimeConfig, AvailableModelInfo, ConversationModel } from "lemma-sdk";
import {
  normalizeAssistantDisplayText,
  normalizeAssistantMarkdown,
} from "lemma-sdk";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, XCircle } from "@/components/ui/icons";
import { humanizeKey } from "./assistant-format";
import { suggestionIconForTitle } from "./assistant-parts";
import type {
  AssistantConversationRenderArgs,
  AssistantMessageRenderArgs,
  AssistantPendingFileRenderArgs,
  AssistantResourceMention,
  LemmaAssistantAppearance,
  LemmaAssistantDensity,
  LemmaAssistantRadius,
} from "./assistant-types";

interface ActiveResourceMentionState {
  query: string;
  start: number;
  end: number;
  items: AssistantResourceMention[];
}

export function getActiveResourceMention(
  draft: string,
  cursor: number,
  mentions: AssistantResourceMention[],
): ActiveResourceMentionState | null {
  if (mentions.length === 0 || cursor < 0) return null;

  const beforeCursor = draft.slice(0, cursor);
  const match = /(^|\s)@([A-Za-z0-9_./:-]*)$/.exec(beforeCursor);
  if (!match) return null;

  const query = match[2].toLowerCase();
  const start = beforeCursor.length - match[2].length - 1;
  const matches = mentions
    .filter((mention) => {
      const searchable = `${mention.label} ${mention.insertText} ${mention.detail ?? ""}`.toLowerCase();
      return searchable.includes(query);
    })
    .slice(0, 8);

  return {
    query,
    start,
    end: cursor,
    items: matches,
  };
}

export function defaultConversationLabel({ conversation }: AssistantConversationRenderArgs): ReactNode {
  return conversation.title || "Untitled conversation";
}

type AssistantRadiusKind = "surface" | "item" | "bubble" | "control";

export function assistantRadiusClassName(radius: LemmaAssistantRadius, kind: AssistantRadiusKind = "surface"): string {
  const map: Record<LemmaAssistantRadius, Record<AssistantRadiusKind, string>> = {
    none: { surface: "rounded-none", item: "rounded-none", bubble: "rounded-none", control: "rounded-none" },
    sm: { surface: "rounded-sm", item: "rounded-sm", bubble: "rounded-sm", control: "rounded-sm" },
    md: { surface: "rounded-md", item: "rounded-md", bubble: "rounded-md", control: "rounded-md" },
    lg: { surface: "rounded-lg", item: "rounded-md", bubble: "rounded-lg", control: "rounded-md" },
    xl: { surface: "rounded-xl", item: "rounded-lg", bubble: "rounded-xl", control: "rounded-lg" },
  };
  return map[radius]?.[kind] ?? map.lg[kind];
}

export function assistantRootClassName(
  appearance: LemmaAssistantAppearance,
  radius: LemmaAssistantRadius,
  showConversationList: boolean,
): string {
  return cn(
    "flex h-full min-h-0 w-full overflow-hidden",
    showConversationList ? "flex-col lg:grid lg:grid-cols-[minmax(16rem,24rem)_minmax(0,1fr)]" : "flex-col",
    appearance === "minimal" || appearance === "borderless"
      ? "border-0 bg-transparent shadow-none"
      : appearance === "contained"
        ? "border border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] bg-[var(--card-bg)] shadow-[var(--shadow-sm)]"
        : "border border-[color:color-mix(in_srgb,var(--border-subtle)_40%,transparent)] bg-[var(--bg-canvas)] shadow-[var(--shadow-sm)]",
    assistantRadiusClassName(radius, "surface"),
  );
}

export function assistantSidebarClassName(appearance: LemmaAssistantAppearance): string {
  return cn(
    "flex min-h-0 flex-col overflow-hidden border-b border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] lg:border-b-0 lg:border-r",
    appearance === "minimal" || appearance === "borderless" ? "bg-transparent" : "bg-[color:color-mix(in_srgb,var(--surface-2)_25%,transparent)]",
  );
}

export function assistantComposerInputClassName(radius: LemmaAssistantRadius, density: LemmaAssistantDensity): string {
  return cn(
    "lemma-assistant-composer-input lemma-assistant-composer-input-shell pod-assistant-inputbar relative flex flex-col gap-2 border-0 backdrop-blur-xl transition-[border-color,box-shadow,transform] duration-200",
    density === "compact" ? "min-h-20 px-4 py-3" : "min-h-24 px-5 py-4",
    radius === "none" ? "rounded-none" : "rounded-2xl",
  );
}

export function shortRuntimeModelName(value?: string | null): string | null {
  if (!value) return null;
  const cleaned = value.split("/").filter(Boolean).pop() || value;
  return cleaned.replace(/^claude-/, "Claude ").replace(/^gpt-/, "GPT ");
}

export function isOpaqueRuntimeId(value?: string | null): boolean {
  if (!value) return false;
  const normalized = value.trim();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(normalized)
    || /^[0-9a-f]{26,}$/i.test(normalized);
}

export function composerRuntimeLabel(
  model: ConversationModel | null,
  runtime: AgentRuntimeConfig | null | undefined,
  options: AvailableModelInfo[],
): string {
  const selectedOption = options.find((option) => {
    if (runtime) {
      const optionRuntime = option.runtime;
      return optionRuntime?.profile_id === runtime.profile_id
        && optionRuntime?.model_name === runtime.model_name;
    }
    return option.id === model;
  });
  const runtimeName = selectedOption?.agentRuntime?.name
    ?? selectedOption?.profile?.name
    ?? null;
  const runtimeFallback = runtime?.profile_id && !isOpaqueRuntimeId(runtime.profile_id)
    ? humanizeKey(runtime.profile_id)
    : null;
  const harness = runtimeName || runtimeFallback;
  const modelLabel = shortRuntimeModelName(runtime?.model_name ?? model ?? null);
  if (harness && modelLabel) return `${harness} · ${modelLabel}`;
  return modelLabel || harness || "";
}

export function stripMarkdownNode<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
  const { node, ...elementProps } = props;
  void node;
  return elementProps;
}

export function markdownComponentsForMessage(isUserMessage: boolean): Components {
  const textClassName = isUserMessage ? "text-current" : "text-[var(--text-primary)]";
  const softTextClassName = isUserMessage ? "text-[color:color-mix(in_srgb,var(--text-on-brand)_85%,transparent)]" : "text-[var(--text-secondary)]";
  const borderClassName = isUserMessage ? "border-[color:color-mix(in_srgb,var(--text-on-brand)_30%,transparent)]" : "border-[color:var(--row-border)]";
  const codeClassName = isUserMessage ? "bg-[color:color-mix(in_srgb,var(--text-on-brand)_15%,transparent)] text-current" : "bg-[var(--surface-2)] text-[var(--text-primary)]";
  const tableHeaderClassName = isUserMessage ? "bg-[color:color-mix(in_srgb,var(--text-on-brand)_10%,transparent)]" : "bg-[color:color-mix(in_srgb,var(--surface-2)_40%,transparent)]";

  return {
    p: ({ className, ...props }) => (
      <p className={cn("my-1.5 leading-6 first:mt-0 last:mb-0", className)} {...stripMarkdownNode(props)} />
    ),
    ul: ({ className, ...props }) => (
      <ul className={cn("my-3 list-disc space-y-1 pl-5 first:mt-0 last:mb-0", className)} {...stripMarkdownNode(props)} />
    ),
    ol: ({ className, ...props }) => (
      <ol className={cn("my-3 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0", className)} {...stripMarkdownNode(props)} />
    ),
    li: ({ className, ...props }) => (
      <li className={cn("pl-1 leading-6", className)} {...stripMarkdownNode(props)} />
    ),
    h1: ({ className, ...props }) => (
      <p className={cn("mb-2 mt-3 text-sm font-semibold leading-6 first:mt-0", textClassName, className)} {...stripMarkdownNode(props)} />
    ),
    h2: ({ className, ...props }) => (
      <p className={cn("mb-2 mt-3 text-sm font-semibold leading-6 first:mt-0", textClassName, className)} {...stripMarkdownNode(props)} />
    ),
    h3: ({ className, ...props }) => (
      <p className={cn("mb-2 mt-3 text-sm font-semibold leading-6 first:mt-0", textClassName, className)} {...stripMarkdownNode(props)} />
    ),
    strong: ({ className, ...props }) => (
      <strong className={cn("font-semibold", textClassName, className)} {...stripMarkdownNode(props)} />
    ),
    em: ({ className, ...props }) => (
      <em className={cn(softTextClassName, className)} {...stripMarkdownNode(props)} />
    ),
    blockquote: ({ className, ...props }) => (
      <blockquote className={cn("my-3 border-l-2 pl-4 first:mt-0 last:mb-0", borderClassName, softTextClassName, className)} {...stripMarkdownNode(props)} />
    ),
    code: ({ className, ...props }) => (
      <code className={cn("rounded px-1 py-0.5 font-mono text-sm", codeClassName, className)} {...stripMarkdownNode(props)} />
    ),
    pre: ({ className, ...props }) => (
      <pre className={cn("my-3 overflow-x-auto rounded-md border p-3 text-xs first:mt-0 last:mb-0", borderClassName, codeClassName, className)} {...stripMarkdownNode(props)} />
    ),
    table: ({ className, ...props }) => (
      <div className="my-3 w-full overflow-x-auto first:mt-0 last:mb-0">
        <table className={cn("w-full min-w-max border-collapse text-sm", className)} {...stripMarkdownNode(props)} />
      </div>
    ),
    th: ({ className, ...props }) => (
      <th className={cn("border px-2 py-1.5 text-left font-semibold", borderClassName, tableHeaderClassName, className)} {...stripMarkdownNode(props)} />
    ),
    td: ({ className, ...props }) => (
      <td className={cn("border px-2 py-1.5 align-top", borderClassName, className)} {...stripMarkdownNode(props)} />
    ),
    a: ({ className, target, rel, ...props }) => (
      <a
        {...stripMarkdownNode(props)}
        className={cn("font-medium underline-offset-4 hover:underline", isUserMessage ? "text-current" : "text-[var(--action-primary)]", className)}
        target={target || "_blank"}
        rel={rel || "noreferrer noopener"}
      />
    ),
  };
}

export function defaultMessageContent({ message }: AssistantMessageRenderArgs): ReactNode {
  const displayContent = normalizeAssistantDisplayText(message.content);
  const suggestionCards = parseAssistantSuggestionCards(displayContent);

  if (suggestionCards) {
    return (
      <div className="flex flex-col gap-4">
        {suggestionCards.headline ? (
          <p className="max-w-2xl text-xl font-medium leading-relaxed tracking-tight text-[var(--text-primary)] sm:text-2xl">
            {suggestionCards.headline}
          </p>
        ) : null}
        <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
          {suggestionCards.cards.map((card) => (
            <div key={card.title} className="rounded-lg border border-[color:color-mix(in_srgb,var(--border-subtle)_70%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-2)_25%,transparent)] p-4 text-left">
              <span className="mb-3 flex size-8 items-center justify-center rounded-md bg-[var(--bg-canvas)] text-[var(--action-primary)]">{card.icon}</span>
              <span className="block text-sm font-semibold text-[var(--text-primary)]">{card.title}</span>
              <span className="mt-1 block text-sm leading-relaxed text-[var(--text-secondary)]">{card.description}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const isUserMessage = message.role === "user";

  return (
    <div className={cn("min-w-0 overflow-hidden break-words text-sm font-normal leading-6 tracking-normal text-[var(--text-primary)]", isUserMessage ? "max-w-prose" : "max-w-full")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={markdownComponentsForMessage(isUserMessage)}
      >
        {normalizeAssistantMarkdown(displayContent)}
      </ReactMarkdown>
    </div>
  );
}

export function isInlineAssistantErrorNoise(errorDetails: string): boolean {
  const normalized = errorDetails.trim().toLowerCase();
  if (!normalized) return false;

  return normalized === "network error"
    || normalized === "failed to fetch"
    || normalized === "fetch failed"
    || normalized.includes("networkerror")
    || normalized.includes("network request failed")
    || normalized.includes("load failed")
    || normalized.includes("failed to load");
}

export function parseAssistantSuggestionCards(content: string): {
  headline: string;
  cards: Array<{ title: string; description: string; icon: ReactNode }>;
} | null {
  const lines = content.split(/\r?\n/);
  const hasStandaloneIntro = lines.some((line) => {
    const normalized = stripInlineMarkdown(line).replace(/^#{1,6}\s+/, "").trim();
    return /^I can (assist|help) with:?$/i.test(normalized);
  });
  if (!hasStandaloneIntro) return null;
  if (lines.some((line) => /^\s*\|.*\|\s*$/.test(line))) return null;

  const introLines: string[] = [];
  const outroLines: string[] = [];
  const cards: Array<{ title: string; description: string; icon: ReactNode }> = [];
  let hasSeenCard = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    const match = trimmed.match(/^[-*]\s+(?:\*\*)?(.+?)(?:\*\*)?\s+(?:\u2014|-)\s+(.+)$/);
    if (match) {
      const title = stripInlineMarkdown(match[1]);
      cards.push({
        title,
        description: stripInlineMarkdown(match[2]),
        icon: suggestionIconForTitle(title),
      });
      hasSeenCard = true;
      continue;
    }

    if (hasSeenCard) {
      outroLines.push(stripInlineMarkdown(trimmed));
    } else {
      introLines.push(stripInlineMarkdown(trimmed));
    }
  }

  if (cards.length < 2 || introLines.length === 0) return null;

  const intro = introLines
    .join(" ")
    .replace(/\s*I can assist with:?\s*$/i, "")
    .trim();
  const outro = outroLines.join(" ").trim();
  const headline = [intro, outro].filter(Boolean).join(" ");

  return { headline, cards };
}

export function stripInlineMarkdown(value: string): string {
  return value
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function assistantChromeStyleFromAppearance(appearance: LemmaAssistantAppearance): "elevated" | "subtle" | "flat" {
  if (appearance === "borderless") return "flat";
  if (appearance === "contained") return "elevated";
  return "subtle";
}

export function defaultPendingFile({ file, remove, status = "queued", error }: AssistantPendingFileRenderArgs): ReactNode {
  const isUploading = status === "uploading";
  const isUploaded = status === "uploaded";
  const isFailed = status === "failed";
  return (
    <Badge
      variant="default"
      className={cn(
        "inline-flex h-6 items-center gap-1.5 px-2 text-xs",
        isFailed && "lemma-assistant-pending-file-error",
      )}
      title={error || file.name}
    >
      {isUploading ? (
        <span className="size-2.5 animate-spin rounded-full border border-[var(--text-tertiary)] border-t-transparent" aria-hidden="true" />
      ) : isUploaded ? (
        <CheckCircle2 className="size-3 text-[var(--state-success)]" aria-hidden="true" />
      ) : isFailed ? (
        <XCircle className="size-3" aria-hidden="true" />
      ) : null}
      <span className="truncate max-w-[160px]">{file.name}</span>
      {!isUploading ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={remove}
          className="inline-flex size-4 items-center justify-center rounded-sm text-[var(--text-secondary)] hover:bg-[color:color-mix(in_srgb,var(--surface-2)_80%,transparent)] hover:text-[var(--text-primary)]"
          title="Remove file"
        >
          ×
        </Button>
      ) : null}
    </Badge>
  );
}
