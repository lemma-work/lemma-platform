"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  ArrowUp,
  BarChart3,
  CheckSquare,
  ChevronDown,
  Database,
  FileOutput,
  FileText,
  Mail,
  Square,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  previewAgentOutputValue,
  schemaFieldKeys,
} from "@/lib/utils/agent-output";
import type { DisplayResourceRequest } from "@/lib/assistant/display-resource";
import { humanizeKey, reasoningPartLabel } from "./assistant-format";
import type {
  AssistantFinalOutputRenderArgs,
  EmptyStateSuggestion,
  LemmaAssistantDensity,
} from "./assistant-types";
import type { PlanSummaryState } from "./assistant-experience";

export function suggestionIconForTitle(title: string): ReactNode {
  const normalized = title.toLowerCase();
  const className = "size-4";
  if (normalized.includes("contact") || normalized.includes("company")) return <Users className={className} />;
  if (normalized.includes("deal") || normalized.includes("pipeline")) return <BarChart3 className={className} />;
  if (normalized.includes("email") || normalized.includes("thread")) return <Mail className={className} />;
  if (normalized.includes("task") || normalized.includes("reminder")) return <CheckSquare className={className} />;
  return <ArrowUp className={className} />;
}

export function DefaultFinalOutputPanel({ output, schema }: AssistantFinalOutputRenderArgs): ReactNode {
  const keys = schemaFieldKeys(schema, output).filter((key) => typeof output[key] !== "undefined");

  return (
    <details open className="group w-fit max-w-full min-w-[min(100%,420px)] rounded-lg border border-[color:color-mix(in_srgb,var(--row-border)_70%,transparent)] bg-[var(--bg-canvas)] shadow-[var(--shadow-sm)]">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[var(--action-primary-soft)] text-[var(--action-primary)]">
            <FileOutput className="size-3.5" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-[var(--text-primary)]">Result</span>
            <span className="block truncate text-xs text-[var(--text-secondary)]">Structured final answer</span>
          </span>
        </span>
        <ChevronDown className="size-3.5 shrink-0 text-[var(--text-tertiary)] transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>

      <div className="px-3 pb-3">
        {keys.length > 0 ? (
          <div className={cn("grid max-w-3xl gap-2", keys.length > 1 && "sm:grid-cols-2")}>
            {keys.map((key) => (
              <div key={key} className="max-w-2xl rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_50%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-2)_20%,transparent)] px-2.5 py-2">
                <p className="text-xs font-medium text-[var(--text-secondary)]">{humanizeKey(key)}</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-5 text-[var(--text-primary)]">
                  {previewAgentOutputValue(output[key])}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-[var(--text-secondary)]">No result fields were returned.</p>
        )}
      </div>
    </details>
  );
}

export function PlanSummaryStrip({ plan, onHide }: { plan: PlanSummaryState; onHide: () => void }) {
  const [showDetails, setShowDetails] = useState(false);
  const visibleSteps = showDetails ? plan.steps : [];

  return (
    <div className="flex w-full min-w-0 max-w-full flex-col gap-1.5 overflow-hidden rounded-lg border border-[color:color-mix(in_srgb,var(--row-border)_80%,transparent)] bg-[var(--bg-canvas)] px-2.5 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="shrink-0 text-xs font-semibold text-[var(--text-primary)]">Plan</span>
        <span className="shrink-0 text-xs text-[var(--text-secondary)]">
          {plan.completedCount}/{plan.steps.length} complete
        </span>
        {plan.inProgressCount > 0 ? (
          <Badge variant="default" className="lemma-assistant-plan-active-badge h-5 shrink-0 px-1.5 text-xs">
            {plan.inProgressCount} active
          </Badge>
        ) : null}
        {plan.activeStep ? (
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-secondary)]" title={plan.activeStep}>
            {plan.running ? "Running:" : "Current:"} {plan.activeStep}
          </span>
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        {plan.steps.length > 0 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setShowDetails((prev) => !prev)}
            className="h-6 shrink-0 px-2 text-xs"
          >
            {showDetails ? "Less" : "Details"}
          </Button>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onHide}
          className="h-6 shrink-0 px-2 text-xs"
        >
          Hide
        </Button>
      </div>

      {showDetails ? (
        <div className="flex flex-col gap-1 border-t border-[color:color-mix(in_srgb,var(--row-border)_60%,transparent)] pt-1.5">
          {visibleSteps.map((step, index) => (
            <div
              key={`${step.step}-${index}`}
              className="flex items-start gap-2 text-xs"
              data-status={step.status}
            >
              <span className={cn(
                "size-2 rounded-full flex-shrink-0 mt-0.5",
                step.status === "completed" && "status-dot-success",
                step.status === "in_progress" && "bg-[var(--action-primary)]",
                step.status === "pending" && "bg-[var(--row-border)]",
              )} />
              <span className={cn(
                step.status === "completed" && "text-[var(--text-secondary)] line-through",
                step.status === "in_progress" && "font-medium text-[var(--action-primary)]",
                step.status === "pending" && "lemma-assistant-text-primary-soft",
              )}>
                {step.step}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export interface ThinkingIndicatorProps {
  label?: string;
  shimmer?: boolean;
}

export function ThinkingIndicator({
  label = "Thinking",
  shimmer = true,
}: ThinkingIndicatorProps = {}) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setShow(true), 350);
    return () => clearTimeout(timer);
  }, []);

  if (!show) return null;

  if (!shimmer) {
    return (
      <div className="px-1 text-sm font-normal text-[var(--text-secondary)]" role="status" aria-live="polite">
        {label}
      </div>
    );
  }

  return (
    <div role="status" aria-live="polite" aria-label="Generating response">
      <span
        className="lemma-assistant-thinking-shimmer inline-block bg-clip-text text-sm font-normal text-transparent animate-[lemma-skeleton-breathe_1.5s_ease-in-out_infinite]"
      >
        {label}
      </span>
    </div>
  );
}

export interface EmptyStateProps {
  onSendMessage: (msg: string) => void;
  suggestions?: EmptyStateSuggestion[];
  density?: LemmaAssistantDensity;
}

export const DEFAULT_EMPTY_STATE_SUGGESTIONS: EmptyStateSuggestion[] = [
  { text: "Help me get started", icon: "→" },
  { text: "Summarize this for me", icon: "✦" },
  { text: "Help me draft a reply", icon: "✎" },
  { text: "Brainstorm next steps", icon: "⋯" },
];

export function LemmaMarkIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M10 2.5 16.25 5v4.85c0 4.25-2.55 7.05-6.25 8.15-3.7-1.1-6.25-3.9-6.25-8.15V5L10 2.5Z"
        fill="currentColor"
        fillOpacity="0.18"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path
        d="m7.1 10.1 1.8 1.8 4-4.1"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function LemmaMiniMark({ className }: { className?: string }) {
  return (
    <span
      className={cn("inline-flex items-end gap-[2px] text-[var(--delight)]", className)}
      aria-hidden="true"
    >
      <span className="block h-[5px] w-[2px] rounded-sm bg-current" />
      <span className="block h-[9px] w-[2px] rounded-sm bg-current" />
      <span className="block h-[13px] w-[2px] rounded-sm bg-current" />
    </span>
  );
}

export function EmptyState({
  onSendMessage,
  suggestions = DEFAULT_EMPTY_STATE_SUGGESTIONS,
  density = "comfortable",
}: EmptyStateProps) {
  const isCompact = density === "compact";

  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center justify-center px-4 text-center",
        isCompact ? "min-h-[min(20rem,46vh)] gap-3 py-5" : "min-h-[min(30rem,58vh)] gap-4 py-8",
      )}
    >
      <div className={cn("flex max-w-2xl flex-col items-center", isCompact ? "gap-1.5" : "gap-2")}>
        <div className="flex items-center gap-1.5 text-xs font-normal text-[var(--text-secondary)]">
          <LemmaMiniMark />
          Lemma Assist
        </div>
        <h4 className={cn("lemma-assistant-text-heading font-normal tracking-tight", isCompact ? "text-base" : "text-lg")}>
          What do you want to make happen?
        </h4>
        <p className={cn("max-w-xl text-[var(--text-secondary)]", isCompact ? "text-sm leading-5" : "text-sm leading-6")}>
          Describe the outcome, paste context, or start with one of these moves.
        </p>
      </div>

      <div className="flex w-full max-w-2xl flex-wrap justify-center gap-2">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion.text}
            type="button"
            className="lemma-assistant-empty-state-suggestion-button group inline-flex max-w-full items-center gap-2 rounded-md border px-3 py-2 text-left text-sm font-normal shadow-[var(--shadow-sm)] transition-colors"
            onClick={() => onSendMessage(suggestion.text)}
          >
            <span className="lemma-assistant-empty-state-suggestion-icon flex size-5 shrink-0 items-center justify-center rounded border text-xs transition-colors">
              {suggestion.icon || "↗"}
            </span>
            <span className="min-w-0 truncate">{suggestion.text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function ReasoningPartCard({
  text,
  isStreaming,
  durationMs,
}: {
  text: string;
  isStreaming: boolean;
  durationMs?: number;
}) {
  const label = reasoningPartLabel(isStreaming, durationMs);

  return (
    <details className="flex flex-col gap-1">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-sm leading-5 text-[var(--text-secondary)]">
        <span
          className={cn("font-normal text-[var(--text-secondary)]", isStreaming && "animate-pulse text-[var(--action-primary)]")}
        >
          {label}
        </span>
      </summary>
      <div className="mt-1 border-l border-[color:var(--row-border)] pl-4">
        <pre className="whitespace-pre-wrap font-mono text-xs text-[var(--text-secondary)]">{text}</pre>
      </div>
    </details>
  );
}

export function displayResourceIcon(request: DisplayResourceRequest): ReactNode {
  const className = "size-3.5";
  switch (request.type) {
    case "FILE":
      return <FileText className={className} />;
    case "TABLE":
      return <Database className={className} />;
    case "AGENT":
      return <Users className={className} />;
    case "FUNCTION":
      return <FileOutput className={className} />;
    case "WORKFLOW":
      return <CheckSquare className={className} />;
    case "WIDGET":
      return <BarChart3 className={className} />;
    case "APP":
    case "SCHEDULE":
    default:
      return <Square className={className} />;
  }
}
