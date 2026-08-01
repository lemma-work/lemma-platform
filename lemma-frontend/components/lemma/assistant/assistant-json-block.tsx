"use client";

// JSON that arrives in a chat message — fenced or bare — rendered as a labelled,
// copyable, syntax-colored block instead of a wall of markdown text. Detection
// and formatting live in lib/assistant/json-blocks; this file is presentation.

import { useMemo, useState } from "react";
import { Check, ChevronDown, Code, Copy } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { playSoundFeedback } from "@/lib/feedback/sound-feedback";
import {
  tokenizeAssistantJson,
  type AssistantJsonPayload,
  type AssistantJsonTokenKind,
} from "@/lib/assistant/json-blocks";

/** Longer payloads open collapsed so one tool dump cannot bury the reply. */
const COLLAPSED_LINE_LIMIT = 24;
const PREVIEW_CHARS = 120;

const TOKEN_CLASS_NAMES: Record<AssistantJsonTokenKind, string> = {
  key: "text-[var(--action-primary)]",
  string: "text-[var(--state-success)]",
  number: "text-[var(--state-info)]",
  boolean: "text-[var(--state-warning)]",
  null: "text-[var(--text-tertiary)]",
  punctuation: "text-[var(--text-tertiary)]",
  plain: "",
};

export function AssistantJsonBlock({
  json,
  isUserMessage = false,
}: {
  json: AssistantJsonPayload;
  isUserMessage?: boolean;
}) {
  const [isExpanded, setIsExpanded] = useState(json.lineCount <= COLLAPSED_LINE_LIMIT);
  const [copied, setCopied] = useState(false);
  const tokens = useMemo(() => tokenizeAssistantJson(json.formatted), [json.formatted]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(json.formatted);
      setCopied(true);
      playSoundFeedback("action-success");
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard access denied */ }
  };

  // Inside a user bubble the brand fill owns the palette, so structure carries the
  // block and the syntax colors sit this one out.
  const borderClassName = isUserMessage
    ? "border-[color:color-mix(in_srgb,var(--text-on-brand)_30%,transparent)]"
    : "border-[color:var(--row-border)]";
  const surfaceClassName = isUserMessage
    ? "bg-[color:color-mix(in_srgb,var(--text-on-brand)_12%,transparent)]"
    : "bg-[color:color-mix(in_srgb,var(--surface-2)_50%,transparent)]";
  const headerTextClassName = isUserMessage ? "text-current" : "text-[var(--text-secondary)]";

  return (
    <div className={cn("my-3 overflow-hidden rounded-md border first:mt-0 last:mb-0", borderClassName, surfaceClassName)}>
      <div className={cn("flex items-center gap-2 px-2.5 py-1 text-xs", headerTextClassName)}>
        <Code className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="font-medium">JSON</span>
        <span className="truncate opacity-70">{json.summary}</span>
        <span className="ml-auto flex shrink-0 items-center gap-0.5">
          <Button
            type="button"
            variant="quiet"
            size="icon"
            className="size-6 text-current"
            onClick={handleCopy}
            title="Copy JSON"
            aria-label="Copy JSON"
          >
            {copied
              ? <Check className={cn("size-3.5", isUserMessage ? "text-current" : "text-[var(--state-success)]")} aria-hidden="true" />
              : <Copy className="size-3.5" aria-hidden="true" />}
          </Button>
          <Button
            type="button"
            variant="quiet"
            size="icon"
            className="size-6 text-current"
            onClick={() => setIsExpanded((value) => !value)}
            aria-expanded={isExpanded}
            title={isExpanded ? "Collapse JSON" : `Expand JSON (${json.lineCount} lines)`}
            aria-label={isExpanded ? "Collapse JSON" : "Expand JSON"}
          >
            <ChevronDown className={cn("size-3.5 transition-transform", !isExpanded && "-rotate-90")} aria-hidden="true" />
          </Button>
        </span>
      </div>
      {isExpanded ? (
        <pre className="max-h-96 overflow-auto px-3 pb-2.5 pt-0.5 font-mono text-xs leading-5">
          <code>
            {tokens.map((token, index) => (
              token.kind === "plain"
                ? token.text
                : (
                  <span
                    key={`${index}-${token.kind}`}
                    className={isUserMessage ? "text-current" : TOKEN_CLASS_NAMES[token.kind]}
                  >
                    {token.text}
                  </span>
                )
            ))}
          </code>
        </pre>
      ) : (
        <Button
          type="button"
          variant="quiet"
          size="sm"
          onClick={() => setIsExpanded(true)}
          className="h-auto w-full justify-start gap-2 px-3 pb-2 pt-0.5 font-mono text-xs font-normal"
        >
          <span className="min-w-0 flex-1 truncate text-left opacity-80">{previewOf(json)}</span>
          <span className="shrink-0 opacity-70">{json.lineCount} lines</span>
        </Button>
      )}
    </div>
  );
}

function previewOf(json: AssistantJsonPayload): string {
  const compact = json.raw.replace(/\s+/g, " ").trim();
  return compact.length > PREVIEW_CHARS ? `${compact.slice(0, PREVIEW_CHARS)}…` : compact;
}
