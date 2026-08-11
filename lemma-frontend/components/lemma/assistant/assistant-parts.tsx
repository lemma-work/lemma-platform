"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  ArrowUp,
  ArrowUpRight,
  BarChart3,
  CheckSquare,
  ChevronDown,
  Table,
  FileOutput,
  FileText,
  Mail,
  MoreHorizontal,
  Pencil,
  Sparkles,
  Square,
  Users,
} from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { DisplayResourceRequest } from "@/lib/assistant/display-resource";
import { reasoningPartLabel } from "./assistant-format";
import type {
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
        ) : plan.nextStep ? (
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-secondary)]" title={plan.nextStep}>
            Next: {plan.nextStep}
          </span>
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        {plan.steps.length > 0 ? (
          <Button
            type="button"
            variant="quiet"
            size="sm"
            onClick={() => setShowDetails((prev) => !prev)}
            className="h-6 shrink-0 px-2 text-xs"
          >
            {showDetails ? "Less" : "Details"}
          </Button>
        ) : null}
        <Button
          type="button"
          variant="quiet"
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

  // The 350ms wait is still right — a reply that starts immediately should not
  // flash the word "Thinking" first. What was wrong is that it used to return
  // null and take up no room, so the transcript jumped a line when the label
  // appeared and jumped again when the first token replaced it. Now the line is
  // reserved from the start and only its contents fade in.
  //
  // A span, not a div: the tool rollup renders this inside its toggle <button>,
  // which only admits phrasing content.
  //
  // No horizontal padding: this line sits in the same left rail as the message
  // text, the tool rows and the run-trace header. A 4px inset here made every
  // live "Working · …" line hang right of its neighbours, and shifted the line
  // back left the moment the run settled and the plain label replaced it.
  return (
    <span
      className="lemma-assistant-thinking inline-flex h-5 items-center"
      role="status"
      aria-live="polite"
      aria-label={show ? "Generating response" : undefined}
      data-visible={show ? "true" : undefined}
    >
      {show ? (
        shimmer ? (
          <span className="lemma-assistant-thinking-shimmer inline-block bg-clip-text text-sm font-normal text-transparent animate-[lemma-skeleton-breathe_1.5s_ease-in-out_infinite]">
            {label}
          </span>
        ) : (
          <span className="text-sm font-normal text-[var(--text-secondary)]">{label}</span>
        )
      ) : null}
    </span>
  );
}

export interface EmptyStateProps {
  onSendMessage: (msg: string) => void;
  suggestions?: EmptyStateSuggestion[];
  density?: LemmaAssistantDensity;
}

export const DEFAULT_EMPTY_STATE_SUGGESTIONS: EmptyStateSuggestion[] = [
  { text: "Help me get started", icon: <ArrowUpRight className="size-3.5" aria-hidden="true" /> },
  { text: "Summarize this for me", icon: <Sparkles className="size-3.5" aria-hidden="true" /> },
  { text: "Help me draft a reply", icon: <Pencil className="size-3.5" aria-hidden="true" /> },
  { text: "Brainstorm next steps", icon: <MoreHorizontal className="size-3.5" aria-hidden="true" /> },
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
              {suggestion.icon || <ArrowUpRight className="size-3.5" aria-hidden="true" />}
            </span>
            <span className="min-w-0 truncate">{suggestion.text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The one line style for anything collapsible in the transcript.
 *
 * A run rollup ("Worked for 1m 7s"), a thought ("Thought"), a tool group
 * ("Terminal command ×8") are the same object to a reader: a summary you can
 * open. They had four separate implementations — two chevron sizes, one with no
 * chevron at all, one with a rule under it — so a single turn showed four
 * treatments of one idea. One component, one size, one colour, one chevron.
 */
export function TraceDisclosureLine({
  label,
  isExpanded,
  onToggle,
  shimmer = false,
  rule = false,
}: {
  label: string;
  isExpanded?: boolean;
  onToggle?: () => void;
  shimmer?: boolean;
  rule?: boolean;
}) {
  const body = (
    <>
      {shimmer
        ? <ThinkingIndicator label={label} shimmer />
        : <span className="min-w-0 break-words">{label}</span>}
      {onToggle ? (
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-[var(--text-tertiary)] transition-transform",
            !isExpanded && "-rotate-90",
          )}
          aria-hidden="true"
        />
      ) : null}
    </>
  );

  const lineClassName = "lemma-assistant-run-trace-header flex w-fit max-w-full items-center gap-1.5 border-0 bg-transparent p-0 text-left text-sm leading-5 text-[var(--text-secondary)] transition-colors";

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {onToggle ? (
        <button
          type="button"
          className={cn(lineClassName, "cursor-pointer hover:text-[var(--text-primary)]")}
          onClick={onToggle}
          aria-expanded={isExpanded}
        >
          {body}
        </button>
      ) : (
        <div className={lineClassName}>{body}</div>
      )}
      {/* A hairline, not a divider. This separates a run's trace from the answer
          under it — a hint of structure, not a horizontal rule drawn across the
          reading column. At full `--row-border` it read as the heavier of the
          two, louder than the label it sits beneath. */}
      {rule ? (
        <div
          className="h-px w-full bg-[color:color-mix(in_srgb,var(--row-border)_45%,transparent)]"
          aria-hidden="true"
        />
      ) : null}
    </div>
  );
}

export function ReasoningPartCard({
  text,
  isStreaming,
  durationMs,
  showSummary = true,
}: {
  text: string;
  isStreaming: boolean;
  durationMs?: number;
  showSummary?: boolean;
}) {
  const label = reasoningPartLabel(false, durationMs);
  // Italic, muted, and set as prose — the same voice whether the thought is
  // arriving or being re-read, so opening one does not change its typeface.
  // Identical to trace narration in `defaultMessageContent`, deliberately: a
  // thought and a line of narration are the same agent talking to itself on the
  // way to an answer, and rendering them as two different things was part of why
  // a run read as a stack of unrelated fragments.
  const content = (
    <p className={cn(
      "whitespace-pre-wrap break-words text-sm italic leading-relaxed text-[var(--text-secondary)]",
      showSummary && "mt-1",
    )}>
      {text}
    </p>
  );

  if (!showSummary) return content;

  // A thought that is still arriving is shown, not filed. It used to render as a
  // closed drawer labelled "Thinking" — beside a transcript whose one activity
  // indicator also said "Thinking", which is where the doubled label came from.
  // While it streams, the prose is the indicator.
  if (isStreaming) return content;

  return <CollapsedThought label={label}>{content}</CollapsedThought>;
}

function CollapsedThought({ label, children }: { label: string; children: ReactNode }) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="flex flex-col gap-1">
      <TraceDisclosureLine
        label={label}
        isExpanded={isExpanded}
        onToggle={() => setIsExpanded((previous) => !previous)}
      />
      {isExpanded ? children : null}
    </div>
  );
}

export function displayResourceIcon(request: DisplayResourceRequest): ReactNode {
  const className = "size-3.5";
  switch (request.type) {
    case "FILE":
      return <FileText className={className} />;
    case "TABLE":
      return <Table className={className} />;
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
