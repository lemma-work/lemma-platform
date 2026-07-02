"use client";

// User-approval cards extracted from assistant-message-group.tsx: the request_approval
// param/label helpers plus the three approval surfaces (the full card, the composer
// panel, and the inline call). Consumed by the tool-details panel and the rollup.

import { useCallback, useMemo, useState } from "react";
import { isAskUserToolName, userApprovalResolvedDecision } from "lemma-sdk";
import { Check, CheckCircle2, MessageCircleQuestion, ShieldAlert, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  asRecord,
  asString,
  formatToolDisplayName,
  humanizeKey,
  stringifyAssistantError,
  summarizeToolPayload,
} from "./assistant-format";
import type { AssistantToolInvocation } from "lemma-sdk/react";
import type {
  ToolCardArgs,
  ToolCardResult,
  UserApprovalDecision,
} from "./assistant-experience";

export function toolNameFromApprovalMessage(message?: string): string | null {
  if (!message) return null;
  const match = message.match(/tool\s+["“]([^"”]+)["”]/i);
  return match?.[1]?.trim() || null;
}

export function approvalToolParamsDisplay(args: ToolCardArgs): Array<{ name: string; value: string }> {
  // request_approval args: { tool_name, args, title, reason, payload? }.
  // The nested `args` holds the actual arguments of the tool to be run.
  const toolParams = asRecord(args.args);
  return summarizeToolPayload(toolParams).slice(0, 4).map((entry) => ({
    name: humanizeKey(entry.key),
    value: entry.value,
  }));
}

export function userApprovalDetails(args: ToolCardArgs): {
  title: string;
  request: string;
  serverName?: string;
  toolName?: string;
  kind?: string;
  params: Array<{ name: string; value: string }>;
  canApproveForSession: boolean;
} {
  // request_approval args: { tool_name, args, title, reason, payload?, permission_ids? }.
  const toolName = asString(args.tool_name) || toolNameFromApprovalMessage(asString(args.reason));
  const title = asString(args.title);
  const reason = asString(args.reason);
  const request = reason
    || (toolName ? `Run ${formatToolDisplayName(toolName)}` : "The assistant is requesting permission to continue.");

  return {
    title: title
      || (toolName ? formatToolDisplayName(toolName) : "Approval required"),
    request,
    serverName: toolName ?? undefined,
    toolName: toolName ?? undefined,
    kind: toolName ?? undefined,
    params: approvalToolParamsDisplay(args),
    // "Approve for session" persists the approved action types for this agent
    // in this conversation (backend session-approval store, TTL-bound), so the
    // same action type won't re-prompt until it expires.
    canApproveForSession: true,
  };
}

export function userApprovalDecisionLabel(decision?: string): string {
  if (decision === "APPROVE_FOR_SESSION") return "Approved for session";
  if (decision === "APPROVE_ONCE") return "Approved once";
  if (decision === "DENY") return "Denied";
  return "Resolved";
}

export function UserApprovalCard({
  invocation,
  onResolveUserApproval,
}: {
  invocation: AssistantToolInvocation;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
}) {
  const resultData = (invocation.result || {}) as ToolCardResult;
  const details = userApprovalDetails(invocation.args);
  const [pendingDecision, setPendingDecision] = useState<UserApprovalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const resolvedDecision = userApprovalResolvedDecision(resultData);
  const isResolved = invocation.state === "result" || !!resolvedDecision;
  const isDenied = resolvedDecision === "DENY";
  const canResolve = !!onResolveUserApproval && !isResolved && !pendingDecision;

  const resolve = useCallback(async (decision: UserApprovalDecision) => {
    if (!onResolveUserApproval || pendingDecision) return;
    setPendingDecision(decision);
    setError(null);
    try {
      await onResolveUserApproval(invocation.toolCallId, decision, {});
    } catch (resolveError) {
      setError(stringifyAssistantError(resolveError) || "Could not resolve approval.");
      setPendingDecision(null);
    }
  }, [invocation.toolCallId, onResolveUserApproval, pendingDecision]);

  return (
    <div className="rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_98%,transparent)] p-3.5 shadow-[var(--shadow-sm)]">
      <div className="flex items-start gap-3">
        <span className={cn(
          "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md",
          "bg-[var(--surface-2)] text-[var(--text-secondary)]",
        )}>
          {isResolved ? (isDenied ? <XCircle className="size-4" /> : <CheckCircle2 className="size-4" />) : <ShieldAlert className="size-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm text-[var(--text-primary)]">{details.title}</div>
            <Badge variant={isResolved ? "outline" : "default"} className="lemma-assistant-approval-status-badge h-5 px-1.5 text-xs">
              {isResolved ? userApprovalDecisionLabel(resolvedDecision) : "Needs approval"}
            </Badge>
          </div>
          <p className="mt-1 text-sm leading-5 text-[var(--text-secondary)]">{details.request}</p>

          {details.params.length > 0 ? (
            <dl className="mt-3 grid gap-1.5">
              {details.params.map((entry) => (
                <div key={entry.name} className="grid grid-cols-[minmax(80px,auto)_minmax(0,1fr)] gap-2 text-xs">
                  <dt className="font-semibold text-[var(--text-secondary)]">{entry.name}</dt>
                  <dd className="min-w-0 break-words text-[var(--text-primary)]">{entry.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}

          {error ? (
            <p className="mt-2 text-xs text-[var(--state-error)]">{error}</p>
          ) : null}

          {!isResolved ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                onClick={() => { void resolve("APPROVE_ONCE"); }}
                disabled={!canResolve}
                className="h-8 px-3 text-xs"
              >
                {pendingDecision === "APPROVE_ONCE" ? "Approving..." : "Approve once"}
              </Button>
              {details.canApproveForSession ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => { void resolve("APPROVE_FOR_SESSION"); }}
                  disabled={!canResolve}
                  className="h-8 px-3 text-xs"
                >
                  {pendingDecision === "APPROVE_FOR_SESSION" ? "Approving..." : "Approve session"}
                </Button>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => { void resolve("DENY"); }}
                disabled={!canResolve}
                className="h-8 px-3 text-xs text-[var(--state-error)] hover:text-[var(--state-error)]"
              >
                {pendingDecision === "DENY" ? "Denying..." : "Deny"}
              </Button>
            </div>
          ) : null}

          <details className="mt-2 text-xs">
            <summary className="cursor-pointer list-none text-[var(--text-secondary)] hover:text-[var(--text-primary)]">Approval details</summary>
            <div className="mt-1 overflow-x-auto rounded bg-[color:color-mix(in_srgb,var(--surface-2)_50%,transparent)] p-2">
              <pre className="lemma-assistant-text-primary-readable whitespace-pre-wrap break-words font-mono text-xs">{JSON.stringify(invocation.args, null, 2)}</pre>
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

export function ComposerApprovalPanel({
  invocation,
  onResolveUserApproval,
}: {
  invocation: AssistantToolInvocation;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
}) {
  const resultData = (invocation.result || {}) as ToolCardResult;
  const details = userApprovalDetails(invocation.args);
  const [pendingDecision, setPendingDecision] = useState<UserApprovalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const resolvedDecision = userApprovalResolvedDecision(resultData);
  const isResolved = invocation.state === "result" || !!resolvedDecision;
  const canResolve = !!onResolveUserApproval && !isResolved && !pendingDecision;
  const primaryParam = details.params[0];

  const resolve = useCallback(async (decision: UserApprovalDecision) => {
    if (!onResolveUserApproval || pendingDecision) return;
    setPendingDecision(decision);
    setError(null);
    try {
      await onResolveUserApproval(invocation.toolCallId, decision, {});
    } catch (resolveError) {
      setError(stringifyAssistantError(resolveError) || "Could not resolve approval.");
      setPendingDecision(null);
    }
  }, [invocation.toolCallId, onResolveUserApproval, pendingDecision]);

  return (
    <div className="lemma-assistant-user-approval-card border border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-1)_96%,transparent)] p-4 shadow-[var(--shadow-sm)]">
      <p className="text-sm leading-6 text-[var(--text-primary)]">{details.request}</p>
      {primaryParam ? (
        <div className="mt-3 rounded-lg bg-[color:color-mix(in_srgb,var(--surface-2)_72%,transparent)] px-3 py-2 font-mono text-xs leading-5 text-[var(--text-secondary)]">
          {primaryParam.value}
        </div>
      ) : null}
      {error ? (
        <p className="mt-2 text-xs text-[var(--state-error)]">{error}</p>
      ) : null}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={() => { void resolve("APPROVE_ONCE"); }}
          disabled={!canResolve}
          className="h-9 px-4 text-sm"
        >
          {pendingDecision === "APPROVE_ONCE" ? "Approving..." : "Approve once"}
        </Button>
        {details.canApproveForSession ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => { void resolve("APPROVE_FOR_SESSION"); }}
            disabled={!canResolve}
            className="h-9 px-4 text-sm"
          >
            {pendingDecision === "APPROVE_FOR_SESSION" ? "Approving..." : "Approve session"}
          </Button>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => { void resolve("DENY"); }}
          disabled={!canResolve}
          className="h-9 px-3 text-sm text-[var(--state-error)] hover:text-[var(--state-error)]"
        >
          {pendingDecision === "DENY" ? "Denying..." : "Deny"}
        </Button>
      </div>
    </div>
  );
}

export function InlineUserApprovalCall({
  invocation,
  isSelected,
  onClick,
}: {
  invocation: AssistantToolInvocation;
  isSelected: boolean;
  onClick: () => void;
}) {
  const resultData = (invocation.result || {}) as ToolCardResult;
  const isAsk = isAskUserToolName(invocation.toolName);
  const title = isAsk ? askUserInlineTitle(invocation.args) : userApprovalDetails(invocation.args).title;
  const resolvedDecision = userApprovalResolvedDecision(resultData);
  const isDenied = resolvedDecision === "DENY";
  const isResolved = invocation.state === "result" || !!resolvedDecision || (isAsk && askUserAnswers(resultData) !== null);
  const pendingIcon = isAsk ? <MessageCircleQuestion className="size-3.5" /> : <ShieldAlert className="size-3.5" />;

  return (
    <button
      type="button"
      onClick={onClick}
      className="lemma-assistant-inline-approval-button inline-flex max-w-full items-center gap-2 border-0 bg-transparent p-0 text-left text-sm leading-5 transition-colors hover:text-[var(--text-primary)]"
      data-selected={isSelected}
    >
      <span className="flex size-3.5 flex-shrink-0 items-center justify-center text-[var(--text-tertiary)]" aria-hidden="true">
        {isResolved ? (isDenied ? <XCircle className="size-3.5" /> : <CheckCircle2 className="size-3.5" />) : pendingIcon}
      </span>
      <span className="min-w-0 truncate text-[var(--text-secondary)]">{title}</span>
    </button>
  );
}

// --- ask_user (multiple-choice questions) -----------------------------------

interface AskUserOption {
  label: string;
  description?: string;
  recommended?: boolean;
}

interface AskUserQuestionDef {
  question: string;
  header: string;
  options: AskUserOption[];
  multiSelect: boolean;
}

const ASK_USER_OTHER = "__other__";

export function parseAskUserQuestions(args: ToolCardArgs): AskUserQuestionDef[] {
  const raw = (args as Record<string, unknown>).questions;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry): AskUserQuestionDef => {
      const record = asRecord(entry);
      const options = Array.isArray(record.options)
        ? record.options
            .map((option) => {
              const opt = asRecord(option);
              return {
                label: asString(opt.label) || "",
                description: asString(opt.description) || undefined,
                recommended: opt.recommended === true,
              };
            })
            .filter((option) => option.label)
        : [];
      return {
        question: asString(record.question) || "",
        header: asString(record.header) || "",
        options,
        multiSelect: record.multi_select === true,
      };
    })
    .filter((question) => question.header && question.options.length > 0);
}

function askUserAnswers(resultData: ToolCardResult): Record<string, unknown> | null {
  const answers = asRecord(resultData).answers;
  return answers && typeof answers === "object" ? (answers as Record<string, unknown>) : null;
}

/** Shared interactive question form used by the card and composer surfaces. */
function AskUserQuestionsForm({
  invocation,
  onResolveUserApproval,
  variant,
}: {
  invocation: AssistantToolInvocation;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
  variant: "card" | "composer";
}) {
  const questions = useMemo(() => parseAskUserQuestions(invocation.args), [invocation.args]);
  // Single-select keeps one label (or ASK_USER_OTHER); multi-select keeps a set.
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [multiChoice, setMultiChoice] = useState<Record<string, Set<string>>>({});
  const [other, setOther] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<null | "submit" | "dismiss">(null);
  const [error, setError] = useState<string | null>(null);
  // Show one question at a time; answers persist across navigation.
  const [index, setIndex] = useState(0);

  const answerFor = useCallback((question: AskUserQuestionDef): string | string[] | null => {
    const otherText = (other[question.header] || "").trim();
    if (question.multiSelect) {
      const labels = [...(multiChoice[question.header] ?? new Set<string>())];
      const values = labels.filter((label) => label !== ASK_USER_OTHER);
      if (labels.includes(ASK_USER_OTHER)) {
        if (!otherText) return null;
        values.push(otherText);
      }
      return values.length > 0 ? values : null;
    }
    const selected = choice[question.header];
    if (selected === ASK_USER_OTHER) return otherText || null;
    return selected || null;
  }, [choice, multiChoice, other]);

  const total = questions.length;
  const safeIndex = Math.min(index, Math.max(total - 1, 0));
  const current = questions[safeIndex];
  const isLast = safeIndex >= total - 1;
  const currentAnswered = current ? answerFor(current) !== null : false;
  const allAnswered = questions.every((question) => answerFor(question) !== null);
  const canSubmit = !!onResolveUserApproval && pending === null && allAnswered;
  const canAdvance = pending === null && currentAnswered;

  const submit = useCallback(async (decision: UserApprovalDecision, action: "submit" | "dismiss") => {
    if (!onResolveUserApproval || pending !== null) return;
    setPending(action);
    setError(null);
    try {
      const answers: Record<string, unknown> = {};
      if (action === "submit") {
        for (const question of questions) {
          const value = answerFor(question);
          if (value !== null) answers[question.header] = value;
        }
      }
      await onResolveUserApproval(invocation.toolCallId, decision, { answers });
    } catch (submitError) {
      setError(stringifyAssistantError(submitError) || "Could not submit your answer.");
      setPending(null);
    }
  }, [answerFor, invocation.toolCallId, onResolveUserApproval, pending, questions]);

  const toggleMulti = useCallback((header: string, value: string) => {
    setMultiChoice((prev) => {
      const next = new Set(prev[header] ?? []);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return { ...prev, [header]: next };
    });
  }, []);

  const optionPad = variant === "composer" ? "px-3 py-2" : "px-2.5 py-1.5";

  if (!current) return null;

  const selectedMulti = multiChoice[current.header] ?? new Set<string>();
  const otherSelected = current.multiSelect
    ? selectedMulti.has(ASK_USER_OTHER)
    : choice[current.header] === ASK_USER_OTHER;

  return (
    <div className="flex flex-col gap-4">
      <div key={current.header} className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="h-5 px-1.5 text-xs">{current.header}</Badge>
          {total > 1 ? (
            <span className="text-xs text-[var(--text-tertiary)]">{safeIndex + 1} of {total}</span>
          ) : null}
        </div>
        <p className="text-sm leading-5 text-[var(--text-primary)]">{current.question}</p>
        <div className="flex flex-col gap-1.5">
          {current.options.map((option) => {
            const isSelected = current.multiSelect
              ? selectedMulti.has(option.label)
              : choice[current.header] === option.label;
            return (
              <button
                key={option.label}
                type="button"
                disabled={pending !== null}
                onClick={() => {
                  if (current.multiSelect) toggleMulti(current.header, option.label);
                  else setChoice((prev) => ({ ...prev, [current.header]: option.label }));
                }}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md border text-left transition-all",
                  optionPad,
                  isSelected
                    ? "border-[var(--accent)] bg-[color:color-mix(in_srgb,var(--accent)_8%,transparent)] ring-1 ring-[var(--action-primary)]"
                    : "border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] hover:border-[color:color-mix(in_srgb,var(--accent)_40%,var(--row-border))] hover:bg-[var(--surface-2)]",
                )}
                data-selected={isSelected}
              >
                <span className="min-w-0 flex-1">
                  <span className={cn(
                    "flex flex-wrap items-center gap-1.5 text-sm text-[var(--text-primary)]",
                    isSelected && "font-medium",
                  )}>
                    {option.label}
                    {option.recommended ? (
                      <Badge variant="outline" className="h-4 px-1 text-xs uppercase tracking-wide">Recommended</Badge>
                    ) : null}
                  </span>
                  {option.description ? (
                    <span className="mt-0.5 block text-xs text-[var(--text-secondary)]">{option.description}</span>
                  ) : null}
                </span>
                <span
                  className={cn(
                    "mt-0.5 flex size-4 flex-shrink-0 items-center justify-center rounded-full border transition-colors",
                    isSelected
                      ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--text-on-brand)]"
                      : "border-[color:color-mix(in_srgb,var(--row-border)_70%,transparent)]",
                  )}
                  aria-hidden="true"
                >
                  {isSelected ? <Check className="size-3" strokeWidth={3} /> : null}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            disabled={pending !== null}
            onClick={() => {
              if (current.multiSelect) toggleMulti(current.header, ASK_USER_OTHER);
              else setChoice((prev) => ({ ...prev, [current.header]: ASK_USER_OTHER }));
            }}
            className={cn(
              "flex w-full items-center gap-2 rounded-md border text-left text-sm text-[var(--text-secondary)] transition-colors",
              optionPad,
              otherSelected
                ? "border-[var(--accent)] bg-[color:color-mix(in_srgb,var(--accent)_12%,transparent)]"
                : "border-dashed border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] hover:bg-[var(--surface-2)]",
            )}
            data-selected={otherSelected}
          >
            Other (type your own)
          </button>
          {otherSelected ? (
            <input
              type="text"
              autoFocus
              disabled={pending !== null}
              value={other[current.header] ?? ""}
              onChange={(event) => setOther((prev) => ({ ...prev, [current.header]: event.target.value }))}
              placeholder="Type your answer"
              className="w-full rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] bg-[var(--bg-canvas)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
            />
          ) : null}
        </div>
      </div>

      {error ? <p className="text-xs text-[var(--state-error)]">{error}</p> : null}

      <div className="flex flex-wrap items-center gap-2">
        {safeIndex > 0 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setIndex(safeIndex - 1)}
            disabled={pending !== null}
            className="h-9 px-3 text-sm text-[var(--text-secondary)]"
          >
            Back
          </Button>
        ) : null}
        {isLast ? (
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={() => { void submit("APPROVE_ONCE", "submit"); }}
            disabled={!canSubmit}
            className="h-9 px-4 text-sm"
          >
            {pending === "submit" ? "Submitting..." : "Submit"}
          </Button>
        ) : (
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={() => setIndex(safeIndex + 1)}
            disabled={!canAdvance}
            className="h-9 px-4 text-sm"
          >
            Next
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => { void submit("DENY", "dismiss"); }}
          disabled={!onResolveUserApproval || pending !== null}
          className="h-9 px-3 text-sm text-[var(--text-secondary)]"
        >
          {pending === "dismiss" ? "Dismissing..." : "Dismiss"}
        </Button>
      </div>
    </div>
  );
}

function AskUserResolvedAnswers({ resultData }: { resultData: ToolCardResult }) {
  const answers = askUserAnswers(resultData);
  if (!answers || Object.keys(answers).length === 0) {
    return <p className="text-sm text-[var(--text-secondary)]">Questions dismissed.</p>;
  }
  return (
    <dl className="grid gap-1.5">
      {Object.entries(answers).map(([header, value]) => (
        <div key={header} className="grid grid-cols-[minmax(80px,auto)_minmax(0,1fr)] gap-2 text-xs">
          <dt className="font-semibold text-[var(--text-secondary)]">{header}</dt>
          <dd className="min-w-0 break-words text-[var(--text-primary)]">
            {Array.isArray(value) ? value.join(", ") : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Short title for an ask_user invocation (its first question header). */
export function askUserInlineTitle(args: ToolCardArgs): string {
  const questions = parseAskUserQuestions(args);
  return questions[0]?.header || questions[0]?.question || "Question";
}

export function AskUserCard({
  invocation,
  onResolveUserApproval,
}: {
  invocation: AssistantToolInvocation;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
}) {
  const resultData = (invocation.result || {}) as ToolCardResult;
  const isResolved = invocation.state === "result" || askUserAnswers(resultData) !== null;

  return (
    <div className="rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_98%,transparent)] p-3.5 shadow-[var(--shadow-sm)]">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)] text-[var(--text-secondary)]">
          {isResolved ? <CheckCircle2 className="size-4" /> : <MessageCircleQuestion className="size-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm text-[var(--text-primary)]">The assistant has a question</div>
            <Badge variant={isResolved ? "outline" : "default"} className="h-5 px-1.5 text-xs">
              {isResolved ? "Answered" : "Needs your input"}
            </Badge>
          </div>
          <div className="mt-3">
            {isResolved ? (
              <AskUserResolvedAnswers resultData={resultData} />
            ) : (
              <AskUserQuestionsForm
                invocation={invocation}
                onResolveUserApproval={onResolveUserApproval}
                variant="card"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function ComposerAskUserPanel({
  invocation,
  onResolveUserApproval,
}: {
  invocation: AssistantToolInvocation;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
}) {
  return (
    <div className="lemma-assistant-user-approval-card border border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-1)_96%,transparent)] p-4 shadow-[var(--shadow-sm)]">
      <AskUserQuestionsForm
        invocation={invocation}
        onResolveUserApproval={onResolveUserApproval}
        variant="composer"
      />
    </div>
  );
}
