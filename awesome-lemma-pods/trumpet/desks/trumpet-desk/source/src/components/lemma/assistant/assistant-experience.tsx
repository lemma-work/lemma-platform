"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ConversationModel } from "lemma-sdk";
import { cn } from "@/components/lemma/lib/utils";
import { Button } from "@/components/lemma/ui/button";
import { Textarea } from "@/components/lemma/ui/textarea";
import { Badge } from "@/components/lemma/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/lemma/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/lemma/ui/dialog";
import { ArrowUp, BarChart3, CheckSquare, Database, FileText, Hash, Mail, Plus, RotateCcw, Search, Square, Users, X } from "lucide-react";
import type {
  AssistantMessagePart,
  AssistantRenderableMessage,
  AssistantToolInvocation,
} from "lemma-sdk/react";
import type {
  AssistantPopupTriggerVariant,
  AssistantPopupPosition,
  AssistantControllerView,
  AssistantConversationRenderArgs,
  AssistantExperienceCustomizationProps,
  AssistantLaunchContextItem,
  AssistantMessageRenderArgs,
  AssistantPendingFileRenderArgs,
  AssistantPresentedFileRenderArgs,
  AssistantToolRenderArgs,
  EmptyStateSuggestion,
  LemmaAssistantAppearance,
  LemmaAssistantDensity,
  LemmaAssistantMode,
  LemmaAssistantRadius,
} from "./assistant-types.js";
import {
  AssistantAskOverlay,
  AssistantComposer,
  AssistantHeader,
  AssistantMessageViewport,
  AssistantModelPicker,
  AssistantStatusPill,
  conversationStatusDotColor,
  relativeTimeAgo,
  type AssistantSurfaceTone,
} from "./assistant-chrome.js";

type ToolCardArgs = Record<string, unknown>;
type ToolCardResult = Record<string, unknown> & {
  success?: boolean;
  resourceType?: string;
  resourceId?: string;
  error?: string;
};

type PlanStatus = "pending" | "in_progress" | "completed";

export interface PlanStepState {
  step: string;
  status: PlanStatus;
}

export interface PlanSummaryState {
  steps: PlanStepState[];
  completedCount: number;
  inProgressCount: number;
  running: boolean;
  activeStep?: string;
}

type AskQuestionType = "single_select" | "multi_select" | "rank_priorities";

export interface AskUserInputQuestion {
  question: string;
  options: string[];
  type: AskQuestionType;
}

export interface PendingAskUserInput {
  toolCallId: string;
  messageIndex: number;
  questions: AskUserInputQuestion[];
}

interface ShowWidgetPayload {
  title?: string;
  widgetCode: string;
  loadingMessages: string[];
}

export interface DisplayMessageRow {
  id: string;
  message: AssistantRenderableMessage;
  sourceIndexes: number[];
}

export interface ActiveToolBanner {
  summary: string;
  activeCount: number;
}

export type AssistantStatusPlacement = "inline" | "composer" | "none";

export interface AssistantExperienceViewProps extends AssistantExperienceCustomizationProps {
  controller: AssistantControllerView;
  mode?: LemmaAssistantMode;
  appearance?: LemmaAssistantAppearance;
  density?: LemmaAssistantDensity;
  chromeStyle?: "elevated" | "subtle" | "flat";
  statusPlacement?: AssistantStatusPlacement;
  radius?: LemmaAssistantRadius;
  showModelPicker?: boolean;
  showNewConversationButton?: boolean;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

function truncateLabel(value: string, max = 72): string {
  const trimmed = value.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

function fileNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || normalized;
}

function formatMessageTimestamp(createdAt?: Date): { text: string; dateTime: string } | null {
  if (!(createdAt instanceof Date) || Number.isNaN(createdAt.getTime())) return null;

  return {
    text: new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(createdAt),
    dateTime: createdAt.toISOString(),
  };
}

function thinkingLabelsFromSummary(summary?: string): string[] {
  const normalized = summary?.toLowerCase() || "";

  if (normalized.includes("search") || normalized.includes("find") || normalized.includes("query")) {
    return ["Searching…", "Working on it…", "Checking results…"];
  }
  if (normalized.includes("plan") || normalized.includes("step")) {
    return ["Planning next steps…", "Working on it…", "Organizing tasks…"];
  }
  if (normalized.includes("run") || normalized.includes("command") || normalized.includes("exec")) {
    return ["Running checks…", "Working on it…", "Inspecting output…"];
  }
  if (normalized.includes("file") || normalized.includes("present")) {
    return ["Preparing files…", "Working on it…", "Finalizing output…"];
  }

  return ["Working on it…", "Thinking…", "Preparing response…"];
}

function toolInvocationKey(tool: AssistantToolInvocation): string {
  return `${tool.toolCallId}:${tool.state}`;
}

export function dedupToolInvocations(message: AssistantRenderableMessage): AssistantToolInvocation[] {
  const invocations: AssistantToolInvocation[] = [];
  const seen = new Set<string>();

  (message.parts || []).forEach((part) => {
    if (part.type !== "tool") return;
    const key = toolInvocationKey(part.toolInvocation);
    if (seen.has(key)) return;
    seen.add(key);
    invocations.push(part.toolInvocation);
  });

  (message.toolInvocations || []).forEach((invocation) => {
    const key = toolInvocationKey(invocation);
    if (seen.has(key)) return;
    seen.add(key);
    invocations.push(invocation);
  });

  return invocations;
}

function normalizePlanStatus(rawStatus: unknown): PlanStatus {
  const status = typeof rawStatus === "string" ? rawStatus.trim().toLowerCase() : "";
  if (status === "completed" || status === "complete" || status === "done") return "completed";
  if (status === "in_progress" || status === "in-progress" || status === "running" || status === "active") return "in_progress";
  return "pending";
}

function parsePlanSteps(value: unknown): PlanStepState[] {
  const entries = asArray(value);
  return entries
    .map((entry, index) => {
      const obj = asRecord(entry);
      const step = asString(obj.step) || asString(obj.title) || `Step ${index + 1}`;
      if (!step) return null;
      return {
        step,
        status: normalizePlanStatus(obj.status),
      };
    })
    .filter((entry): entry is PlanStepState => entry !== null);
}

export function latestPlanSummary(messages: AssistantRenderableMessage[]): PlanSummaryState | null {
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const invocations = dedupToolInvocations(messages[messageIndex]);
    for (let invocationIndex = invocations.length - 1; invocationIndex >= 0; invocationIndex -= 1) {
      const invocation = invocations[invocationIndex];
      if (invocation.toolName.toLowerCase() !== "update_plan") continue;

      const argsObj = asRecord(invocation.args);
      let steps = parsePlanSteps(argsObj.plan);

      if (steps.length === 0) {
        const resultObj = asRecord(invocation.result);
        const outputObj = asRecord(resultObj.output);
        steps = parsePlanSteps(outputObj.plan ?? resultObj.plan);
      }

      if (steps.length === 0) continue;

      const completedCount = steps.filter((step) => step.status === "completed").length;
      const inProgressCount = steps.filter((step) => step.status === "in_progress").length;
      const activeStep = steps.find((step) => step.status === "in_progress")?.step;
      const running = invocation.state !== "result" || inProgressCount > 0;

      return {
        steps,
        completedCount,
        inProgressCount,
        running,
        activeStep,
      };
    }
  }

  return null;
}

function normalizeQuestionType(value: unknown): AskQuestionType {
  const normalized = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (normalized === "multi_select") return "multi_select";
  if (normalized === "rank_priorities") return "rank_priorities";
  return "single_select";
}

function parseAskUserInputQuestions(value: unknown): AskUserInputQuestion[] {
  return asArray(value)
    .map((entry) => {
      const obj = asRecord(entry);
      const question = asString(obj.question);
      const options = asArray(obj.options)
        .map((option) => (typeof option === "string" ? option.trim() : ""))
        .filter((option): option is string => option.length > 0)
        .slice(0, 4);

      if (!question || options.length < 2) {
        return null;
      }

      return {
        question,
        options,
        type: normalizeQuestionType(obj.type),
      };
    })
    .filter((entry): entry is AskUserInputQuestion => entry !== null)
    .slice(0, 3);
}

function extractAskUserInputQuestionsFromInvocation(invocation: AssistantToolInvocation): AskUserInputQuestion[] {
  if (invocation.toolName.toLowerCase() !== "ask_user_input") return [];
  const args = asRecord(invocation.args);
  const result = asRecord(invocation.result);
  const output = asRecord(result.output);

  const fromArgs = parseAskUserInputQuestions(args.questions);
  if (fromArgs.length > 0) return fromArgs;
  const fromResult = parseAskUserInputQuestions(result.questions);
  if (fromResult.length > 0) return fromResult;
  return parseAskUserInputQuestions(output.questions);
}

export function findPendingAskUserInput(messages: AssistantRenderableMessage[]): PendingAskUserInput | null {
  let latestUserMessageIndex = -1;
  messages.forEach((message, index) => {
    if (message.role === "user") {
      latestUserMessageIndex = index;
    }
  });

  let pending: PendingAskUserInput | undefined;
  messages.forEach((message, messageIndex) => {
    if (message.role !== "assistant") return;

    const invocations = dedupToolInvocations(message);
    invocations.forEach((invocation) => {
      const questions = extractAskUserInputQuestionsFromInvocation(invocation);
      if (questions.length === 0) return;
      pending = {
        toolCallId: invocation.toolCallId || `${message.id}-ask-user-input`,
        messageIndex,
        questions,
      };
    });
  });

  if (!pending || latestUserMessageIndex > pending.messageIndex) {
    return null;
  }
  return pending;
}

export function formatAskUserInputAnswers(questions: AskUserInputQuestion[], answers: string[][]): string {
  const rows: string[] = [];
  questions.forEach((question, index) => {
    const answer = answers[index] || [];
    if (answer.length === 0) return;
    const answerText = question.type === "rank_priorities"
      ? answer.join(" > ")
      : answer.join(", ");
    rows.push(`Q: ${question.question}`);
    rows.push(`A: ${answerText}`);
    rows.push("");
  });
  return rows.join("\n").trim();
}

function normalizeFilepaths(value: unknown): string[] {
  return asArray(value)
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter((path): path is string => path.length > 0);
}

export function extractPresentFilePathsFromInvocation(invocation: AssistantToolInvocation): string[] {
  if (invocation.toolName.toLowerCase() !== "present_files") return [];
  const args = asRecord(invocation.args);
  const result = asRecord(invocation.result);
  const output = asRecord(result.output);

  const fromArgs = [
    normalizeFilepaths(args.filepaths),
    normalizeFilepaths(args.file_paths),
    normalizeFilepaths(args.paths),
  ].find((entries) => entries.length > 0) || [];

  if (fromArgs.length > 0) return fromArgs;

  return [
    normalizeFilepaths(result.filepaths),
    normalizeFilepaths(result.file_paths),
    normalizeFilepaths(output.filepaths),
    normalizeFilepaths(output.file_paths),
  ].find((entries) => entries.length > 0) || [];
}

function formatCommandPreview(cmd: string): string {
  const compact = cmd.replace(/\s+/g, " ").trim();
  return truncateLabel(compact, 64);
}

function formatDurationCompact(durationMs: number): string {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  if (minutes <= 0) return `${totalSeconds}s`;
  if (seconds <= 0) return `${minutes}m`;
  return `${minutes}m ${seconds}s`;
}

function primaryToolArgs(args: ToolCardArgs): ToolCardArgs {
  const request = asRecord(args.request);
  if (Object.keys(request).length > 0) return request;

  const waitConfig = asRecord(args.wait_config);
  if (Object.keys(waitConfig).length > 0) return waitConfig;

  return args;
}

function toolArg(args: ToolCardArgs, key: string): unknown {
  const direct = args[key];
  if (typeof direct !== "undefined") return direct;
  return primaryToolArgs(args)[key];
}

function formatToolDisplayName(toolName: string): string {
  return toolName
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function commentLabelFromArgs(args: ToolCardArgs): string | null {
  const comment = asString(toolArg(args, "comment"));
  return comment ? truncateLabel(comment, 72) : null;
}

function toolCallPrimaryLabel(toolName: string, args: ToolCardArgs): string {
  return commentLabelFromArgs(args) || formatToolDisplayName(toolName);
}

function formatActiveToolSummary(toolName: string, args: ToolCardArgs): string {
  const lowerName = toolName.toLowerCase();
  const comment = commentLabelFromArgs(args);

  if (lowerName === "exec_command") {
    if (comment) return `Running ${comment}`;
    const cmd = asString(toolArg(args, "cmd"));
    return cmd ? `Running ${formatCommandPreview(cmd)}` : "Running command";
  }

  if (lowerName === "ask_user_input") {
    const questions = parseAskUserInputQuestions(toolArg(args, "questions"));
    return questions.length > 0
      ? `Waiting for user input (${questions.length} question${questions.length === 1 ? "" : "s"})`
      : "Waiting for user input";
  }

  if (lowerName === "present_files") {
    const filepaths = extractPresentFilePathsFromInvocation({
      toolCallId: "",
      toolName,
      args,
      state: "call",
    });
    return filepaths.length > 0
      ? `Presenting ${filepaths.length} file${filepaths.length === 1 ? "" : "s"}`
      : "Presenting files";
  }

  if (lowerName === "update_plan") {
    const plan = asArray(toolArg(args, "plan"));
    return `Updating plan (${plan.length} step${plan.length === 1 ? "" : "s"})`;
  }

  if (comment) return `Running ${comment}`;

  return `Running ${formatToolDisplayName(toolName)}`;
}

function formatToolResultSummary(toolName: string, args: ToolCardArgs, result: ToolCardResult): string | null {
  const lowerName = toolName.toLowerCase();

  if (lowerName === "present_files") {
    const filepaths = extractPresentFilePathsFromInvocation({
      toolCallId: "",
      toolName,
      args,
      state: "result",
      result,
    });
    if (filepaths.length > 0) {
      return `Presented ${filepaths.length} file${filepaths.length === 1 ? "" : "s"}`;
    }
  }

  if (lowerName === "update_plan") {
    const plan = asArray(toolArg(args, "plan"));
    const completed = plan.filter((step) => asRecord(step).status === "completed").length;
    if (plan.length > 0) {
      return `${completed}/${plan.length} complete`;
    }
  }

  const rawMessage = asString(result.message);
  if (rawMessage) return truncateLabel(rawMessage, 90);

  if (typeof result.error === "string" && result.error.trim()) {
    return truncateLabel(result.error.trim(), 90);
  }

  if (typeof result.resourceType === "string" && typeof result.resourceId === "string") {
    return `Created ${result.resourceType}`;
  }

  return null;
}

function hasMeaningfulTextPart(message: AssistantRenderableMessage): boolean {
  return (message.parts || []).some((part) => part.type === "text" && part.text.trim().length > 0);
}

function isShowWidgetToolName(toolName: string): boolean {
  const normalized = toolName.trim().toLowerCase();
  return normalized === "visualize:show_widget"
    || normalized === "visualize.show_widget"
    || normalized === "show_widget"
    || normalized === "render_widget";
}

function isCollapsibleAssistantMessage(message: AssistantRenderableMessage): boolean {
  if (message.role !== "assistant") return false;
  const hasTools = (message.toolInvocations?.length || 0) > 0 || (message.parts || []).some((part) => part.type === "tool");
  const hasReasoning = (message.parts || []).some((part) => part.type === "reasoning" && part.text.trim().length > 0);
  if (!hasTools && !hasReasoning) return false;
  return !hasMeaningfulTextPart(message) && (!message.content || message.content.trim().length === 0);
}

export function buildDisplayMessageRows(messages: AssistantRenderableMessage[]): DisplayMessageRow[] {
  const rows: DisplayMessageRow[] = [];

  for (let i = 0; i < messages.length; i += 1) {
    const message = messages[i];
    if (!isCollapsibleAssistantMessage(message)) {
      rows.push({
        id: message.id,
        message,
        sourceIndexes: [i],
      });
      continue;
    }

    const cluster: AssistantRenderableMessage[] = [message];
    const sourceIndexes = [i];
    let j = i + 1;
    while (j < messages.length && isCollapsibleAssistantMessage(messages[j])) {
      cluster.push(messages[j]);
      sourceIndexes.push(j);
      j += 1;
    }

    if (cluster.length === 1) {
      rows.push({
        id: message.id,
        message,
        sourceIndexes,
      });
      i = j - 1;
      continue;
    }

    const mergedParts: AssistantMessagePart[] = [];
    const mergedToolInvocations: NonNullable<AssistantRenderableMessage["toolInvocations"]> = [];
    cluster.forEach((entry) => {
      if (entry.parts?.length) {
        mergedParts.push(...entry.parts);
      }
      if (entry.toolInvocations?.length) {
        mergedToolInvocations.push(...entry.toolInvocations);
      }
    });

    rows.push({
      id: `tool-cluster-${cluster[0].id}`,
      message: {
        id: `tool-cluster-${cluster[0].id}`,
        role: "assistant",
        content: "",
        parts: mergedParts,
        toolInvocations: mergedToolInvocations,
        createdAt: cluster[cluster.length - 1]?.createdAt ?? cluster[0]?.createdAt,
      },
      sourceIndexes,
    });

    i = j - 1;
  }

  return rows;
}

export function getActiveToolBanner(messages: AssistantRenderableMessage[]): ActiveToolBanner | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message.role !== "assistant") continue;

    const activeInvocations = dedupToolInvocations(message).filter((invocation) => invocation.state !== "result");
    if (activeInvocations.length === 0) continue;

    const currentInvocation = activeInvocations[activeInvocations.length - 1];
    return {
      summary: formatActiveToolSummary(currentInvocation.toolName, currentInvocation.args),
      activeCount: activeInvocations.length,
    };
  }

  return null;
}

function extractShowWidgetPayloadFromRecord(value: unknown): ShowWidgetPayload | null {
  const record = asRecord(value);
  const widgetCode = asString(record.widget_code);
  if (!widgetCode) {
    return null;
  }

  const loadingMessages = asArray(record.loading_messages)
    .map((entry) => (typeof entry === "string" ? entry.trim() : ""))
    .filter((entry): entry is string => entry.length > 0)
    .slice(0, 4);

  return {
    title: asString(record.title),
    widgetCode,
    loadingMessages,
  };
}

function extractShowWidgetPayload(args: ToolCardArgs, result?: ToolCardResult): ShowWidgetPayload | null {
  const fromArgs = extractShowWidgetPayloadFromRecord(args);
  if (fromArgs) return fromArgs;

  const resultObject = asRecord(result);
  const outputObject = asRecord(resultObject.output);
  const dataObject = asRecord(resultObject.data);
  return extractShowWidgetPayloadFromRecord(outputObject)
    || extractShowWidgetPayloadFromRecord(dataObject)
    || extractShowWidgetPayloadFromRecord(resultObject);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function buildWidgetIframeDocument(toolCallId: string, payload: ShowWidgetPayload): string {
  const widgetCode = payload.widgetCode.trim();
  const isSvg = /^<svg[\s>]/i.test(widgetCode);
  const safeToolCallId = JSON.stringify(toolCallId);
  const safeTitle = escapeHtml(payload.title || "Widget");

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    html, body { margin: 0; padding: 0; width: 100%; background: transparent; color: #0f172a; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .widget-svg-root { width: 100%; display: flex; justify-content: center; align-items: flex-start; overflow: visible; }
    .widget-svg-root > svg { width: 100%; max-width: 100%; height: auto; }
  </style>
  <script>
    (function () {
      var toolCallId = ${safeToolCallId};
      function computeHeight() {
        var doc = document.documentElement;
        var body = document.body;
        return Math.max(
          doc ? doc.scrollHeight : 0,
          body ? body.scrollHeight : 0,
          120
        );
      }
      function reportHeight() {
        parent.postMessage({ type: 'lemma-widget-height', height: Math.max(120, Math.ceil(computeHeight())), toolCallId: toolCallId }, '*');
      }
      window.sendPrompt = function (text) {
        var message = typeof text === 'string' ? text.trim() : '';
        if (!message) return;
        parent.postMessage({ type: 'lemma-widget-send-prompt', text: message, toolCallId: toolCallId }, '*');
      };
      window.addEventListener('load', function () {
        reportHeight();
        setTimeout(reportHeight, 50);
      });
      window.addEventListener('resize', reportHeight);
      if (typeof MutationObserver !== 'undefined' && document.documentElement) {
        var observer = new MutationObserver(reportHeight);
        observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
      }
    })();
  </script>
</head>
<body aria-label="${safeTitle}">
${isSvg ? `<div class="widget-svg-root">${widgetCode}</div>` : widgetCode}
</body>
</html>`;
}

function useControllableDraft(
  controlledValue: string | undefined,
  onChange: ((value: string) => void) | undefined,
): [string, (value: string) => void] {
  const [uncontrolledValue, setUncontrolledValue] = useState("");
  const isControlled = typeof controlledValue === "string";

  const setValue = useCallback((nextValue: string) => {
    if (!isControlled) {
      setUncontrolledValue(nextValue);
    }
    onChange?.(nextValue);
  }, [isControlled, onChange]);

  return [isControlled ? controlledValue : uncontrolledValue, setValue];
}

function defaultConversationLabel({ conversation }: AssistantConversationRenderArgs): ReactNode {
  return conversation.title || "Untitled conversation";
}

type AssistantRadiusKind = "surface" | "item" | "bubble" | "control";

function assistantRadiusClassName(radius: LemmaAssistantRadius, kind: AssistantRadiusKind = "surface"): string {
  const map: Record<LemmaAssistantRadius, Record<AssistantRadiusKind, string>> = {
    none: { surface: "rounded-none", item: "rounded-none", bubble: "rounded-none", control: "rounded-none" },
    sm: { surface: "rounded-sm", item: "rounded-sm", bubble: "rounded-sm", control: "rounded-sm" },
    md: { surface: "rounded-md", item: "rounded-md", bubble: "rounded-md", control: "rounded-md" },
    lg: { surface: "rounded-lg", item: "rounded-md", bubble: "rounded-lg", control: "rounded-md" },
    xl: { surface: "rounded-xl", item: "rounded-lg", bubble: "rounded-xl", control: "rounded-lg" },
  };
  return map[radius]?.[kind] ?? map.lg[kind];
}

function assistantRootClassName(
  mode: LemmaAssistantMode,
  appearance: LemmaAssistantAppearance,
  radius: LemmaAssistantRadius,
  showConversationList: boolean,
): string {
  return cn(
    "flex min-h-0 w-full overflow-hidden",
    mode === "page"
      ? "h-[min(calc(100dvh-8rem),56rem)] min-h-[28rem]"
      : mode === "side-panel"
        ? "h-[min(calc(100dvh-8rem),56rem)] min-h-[28rem] max-w-[34rem]"
        : mode === "popup"
          ? "h-[min(80vh,46rem)] max-h-[80vh]"
        : "h-[36rem] max-h-[75vh]",
    showConversationList ? "flex-col lg:grid lg:grid-cols-[minmax(16rem,24rem)_minmax(0,1fr)]" : "flex-col",
    appearance === "minimal" || appearance === "borderless"
      ? "border-0 bg-transparent shadow-none"
      : appearance === "contained"
        ? "border border-border/60 bg-card shadow-sm"
        : "border border-border/40 bg-background shadow-sm",
    assistantRadiusClassName(radius, "surface"),
  );
}

function assistantPopupPositionClassName(position: AssistantPopupPosition): string {
  if (position === "bottom-left") return "bottom-6 left-6 sm:bottom-8 sm:left-8";
  if (position === "top-right") return "right-6 top-6 sm:right-8 sm:top-8";
  if (position === "top-left") return "left-6 top-6 sm:left-8 sm:top-8";
  return "bottom-6 right-6 sm:bottom-8 sm:right-8";
}

function assistantSidebarClassName(appearance: LemmaAssistantAppearance): string {
  return cn(
    "flex min-h-0 flex-col overflow-hidden border-b border-border/60 lg:border-b-0 lg:border-r",
    appearance === "minimal" || appearance === "borderless" ? "bg-transparent" : "bg-muted/25",
  );
}

function assistantComposerInputClassName(radius: LemmaAssistantRadius): string {
  return cn(
    "relative flex min-h-14 items-center gap-2 border border-border/80 bg-background px-2 py-1.5 focus-within:border-primary/60 focus-within:ring-1 focus-within:ring-primary/20",
    assistantRadiusClassName(radius, "surface"),
  );
}

function launchContextKindLabel(kind: AssistantLaunchContextItem["kind"]): string {
  if (kind === "search_result") return "Search result";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

function launchContextIcon(kind: AssistantLaunchContextItem["kind"]) {
  if (kind === "record") return <Hash className="size-4" />;
  if (kind === "file") return <FileText className="size-4" />;
  if (kind === "table") return <Database className="size-4" />;
  if (kind === "search_result") return <Search className="size-4" />;
  return <BarChart3 className="size-4" />;
}

function plainLabelFromNode(value: ReactNode): string {
  if (value == null || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map((entry) => plainLabelFromNode(entry)).join(" ").trim();
  return "";
}

function normalizeLaunchContext(value?: AssistantLaunchContextItem | AssistantLaunchContextItem[]): AssistantLaunchContextItem[] {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

function defaultLaunchContextPrompt(item: AssistantLaunchContextItem): string {
  const title = plainLabelFromNode(item.title).trim() || launchContextKindLabel(item.kind).toLowerCase();
  return `Help me with this ${launchContextKindLabel(item.kind).toLowerCase()}: ${title}`;
}

function markdownComponentsForMessage(isUserMessage: boolean): Components {
  const textClassName = isUserMessage ? "text-current" : "text-foreground";
  const softTextClassName = isUserMessage ? "text-current/85" : "text-muted-foreground";
  const borderClassName = isUserMessage ? "border-primary-foreground/30" : "border-border";
  const codeClassName = isUserMessage ? "bg-primary-foreground/15 text-current" : "bg-muted";
  const tableHeaderClassName = isUserMessage ? "bg-primary-foreground/10" : "bg-muted/40";

  return {
    p: ({ node: _node, className, ...props }) => (
      <p className={cn("my-2 leading-6 first:mt-0 last:mb-0", className)} {...props} />
    ),
    ul: ({ node: _node, className, ...props }) => (
      <ul className={cn("my-3 list-disc space-y-1 pl-5 first:mt-0 last:mb-0", className)} {...props} />
    ),
    ol: ({ node: _node, className, ...props }) => (
      <ol className={cn("my-3 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0", className)} {...props} />
    ),
    li: ({ node: _node, className, ...props }) => (
      <li className={cn("pl-1 leading-6", className)} {...props} />
    ),
    h1: ({ node: _node, className, ...props }) => (
      <p className={cn("mb-2 mt-3 text-sm font-semibold leading-6 first:mt-0", textClassName, className)} {...props} />
    ),
    h2: ({ node: _node, className, ...props }) => (
      <p className={cn("mb-2 mt-3 text-sm font-semibold leading-6 first:mt-0", textClassName, className)} {...props} />
    ),
    h3: ({ node: _node, className, ...props }) => (
      <p className={cn("mb-2 mt-3 text-sm font-semibold leading-6 first:mt-0", textClassName, className)} {...props} />
    ),
    strong: ({ node: _node, className, ...props }) => (
      <strong className={cn("font-semibold", textClassName, className)} {...props} />
    ),
    em: ({ node: _node, className, ...props }) => (
      <em className={cn(softTextClassName, className)} {...props} />
    ),
    blockquote: ({ node: _node, className, ...props }) => (
      <blockquote className={cn("my-3 border-l-2 pl-4 first:mt-0 last:mb-0", borderClassName, softTextClassName, className)} {...props} />
    ),
    code: ({ node: _node, className, ...props }) => (
      <code className={cn("rounded px-1 py-0.5 font-mono text-[0.85em]", codeClassName, className)} {...props} />
    ),
    pre: ({ node: _node, className, ...props }) => (
      <pre className={cn("my-3 overflow-x-auto rounded-md border p-3 text-xs first:mt-0 last:mb-0", borderClassName, codeClassName, className)} {...props} />
    ),
    table: ({ node: _node, className, ...props }) => (
      <div className="my-3 w-full overflow-x-auto first:mt-0 last:mb-0">
        <table className={cn("w-full min-w-max border-collapse text-sm", className)} {...props} />
      </div>
    ),
    th: ({ node: _node, className, ...props }) => (
      <th className={cn("border px-2 py-1.5 text-left font-semibold", borderClassName, tableHeaderClassName, className)} {...props} />
    ),
    td: ({ node: _node, className, ...props }) => (
      <td className={cn("border px-2 py-1.5 align-top", borderClassName, className)} {...props} />
    ),
    a: ({ node: _node, ...props }) => (
      <a
        {...props}
        className={cn("font-medium underline-offset-4 hover:underline", isUserMessage ? "text-current" : "text-primary", props.className)}
        target={props.target || "_blank"}
        rel={props.rel || "noreferrer noopener"}
      />
    ),
  };
}

function defaultMessageContent({ message }: AssistantMessageRenderArgs): ReactNode {
  const suggestionCards = parseAssistantSuggestionCards(message.content);

  if (suggestionCards) {
    return (
      <div className="flex flex-col gap-4">
        {suggestionCards.headline ? (
          <p className="max-w-2xl text-xl font-medium leading-relaxed tracking-tight text-foreground sm:text-2xl">
            {suggestionCards.headline}
          </p>
        ) : null}
        <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
          {suggestionCards.cards.map((card) => (
            <div key={card.title} className="rounded-lg border border-border/70 bg-muted/25 p-4 text-left">
              <span className="mb-3 flex size-8 items-center justify-center rounded-md bg-background text-primary">{card.icon}</span>
              <span className="block text-sm font-semibold text-foreground">{card.title}</span>
              <span className="mt-1 block text-sm leading-relaxed text-muted-foreground">{card.description}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const isUserMessage = message.role === "user";

  return (
    <div className={cn("min-w-0 max-w-full overflow-hidden break-words text-sm leading-6", isUserMessage ? "text-primary-foreground" : "text-foreground")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={markdownComponentsForMessage(isUserMessage)}
      >
        {normalizeAssistantMarkdown(message.content)}
      </ReactMarkdown>
    </div>
  );
}

function normalizeAssistantMarkdown(content: string): string {
  const trimmed = content.trim();
  const isCompactMarkdown = trimmed.split(/\r?\n/).length <= 2 && /(?:[ \t]---[ \t]|[ \t]#{1,6}\s|\|\s+\|)/.test(trimmed);
  const normalized = trimmed
    .replace(/[ \t]+---[ \t]+/g, "\n\n")
    .replace(/([.!?)\]])[ \t]+(?=#{1,6}\s)/g, "$1\n\n")
    .replace(/[ \t]+(?=#{1,6}\s)/g, "\n\n")
    .replace(/\|\s+\|/g, "|\n|")
    .replace(/\|\s+(?=\|?\s*:?-{3,})/g, "|\n")
    .replace(/(\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|)\s+/g, "$1\n");

  if (!isCompactMarkdown) return normalized;

  return normalized
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/(^|\n)([^|\n]{3,120}?)\s+(\|[^\n]+\|)(?=\n\|?\s*:?-{3,})/g, "$1$2\n\n$3");
}

function parseAssistantSuggestionCards(content: string): {
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
    .replace(/\byour CRM pod\b/i, "your CRM")
    .trim();
  const outro = outroLines.join(" ").trim();
  const headline = [intro, outro].filter(Boolean).join(" ");

  return { headline, cards };
}

function stripInlineMarkdown(value: string): string {
  return value
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function suggestionIconForTitle(title: string): ReactNode {
  const normalized = title.toLowerCase();
  const className = "size-4";
  if (normalized.includes("contact") || normalized.includes("company")) return <Users className={className} />;
  if (normalized.includes("deal") || normalized.includes("pipeline")) return <BarChart3 className={className} />;
  if (normalized.includes("email") || normalized.includes("thread")) return <Mail className={className} />;
  if (normalized.includes("task") || normalized.includes("reminder")) return <CheckSquare className={className} />;
  return <ArrowUp className={className} />;
}

function assistantChromeStyleFromAppearance(appearance: LemmaAssistantAppearance): "elevated" | "subtle" | "flat" {
  if (appearance === "borderless") return "flat";
  if (appearance === "contained") return "elevated";
  return "subtle";
}

function defaultPresentedFile({ filepath }: AssistantPresentedFileRenderArgs): ReactNode {
  return (
    <div className="rounded-lg border border-border/60 bg-card/70 p-3 shadow-none">
      <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
        <FileText className="size-3.5" />
        <span>Presented file</span>
      </div>
      <div className="mt-2 text-sm font-medium text-foreground">{fileNameFromPath(filepath)}</div>
      <div className="mt-1 break-words text-xs text-muted-foreground">{filepath}</div>
    </div>
  );
}

function defaultPendingFile({ file, remove }: AssistantPendingFileRenderArgs): ReactNode {
  return (
    <span className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-md border border-border/60 bg-background px-2 text-xs text-foreground shadow-none">
      <FileText className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="max-w-[160px] truncate">{file.name}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={remove}
        className="inline-flex items-center justify-center size-4 rounded-sm text-muted-foreground hover:text-foreground hover:bg-muted/80"
        title="Remove file"
      >
        ×
      </Button>
    </span>
  );
}

export function PlanSummaryStrip({ plan, onHide }: { plan: PlanSummaryState; onHide: () => void }) {
  const [showAll, setShowAll] = useState(false);
  const visibleSteps = showAll ? plan.steps : plan.steps.slice(0, 5);
  const hiddenCount = Math.max(0, plan.steps.length - visibleSteps.length);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-card/70 p-3.5 shadow-none">
      <div className="flex items-center justify-between gap-2 border-b border-border/50 pb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-widest text-foreground/80">Plan</span>
          <span className="text-xs text-muted-foreground">
            {plan.completedCount}/{plan.steps.length} complete
          </span>
          {plan.inProgressCount > 0 ? (
            <Badge variant="secondary" className="text-xs">
              {plan.inProgressCount} active
            </Badge>
          ) : null}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onHide}
          className="h-6 text-xs px-2"
        >
          Hide
        </Button>
      </div>

      {plan.activeStep ? (
        <div className="mt-1.5 text-xs text-foreground/70 truncate" title={plan.activeStep}>
          {plan.running ? "Running:" : "Current:"} {plan.activeStep}
        </div>
      ) : null}

      <div className="flex flex-col gap-1.5">
        {visibleSteps.map((step, index) => (
          <div
            key={`${step.step}-${index}`}
            className="flex items-start gap-2 rounded-md px-2 py-1 text-xs"
            data-status={step.status}
          >
            <span className={cn(
              "size-2 rounded-full flex-shrink-0 mt-0.5",
              step.status === "completed" && "bg-green-500",
              step.status === "in_progress" && "bg-primary",
              step.status === "pending" && "bg-border",
            )} />
            <span className={cn(
              step.status === "completed" && "text-muted-foreground line-through",
              step.status === "in_progress" && "text-primary font-medium",
              step.status === "pending" && "text-foreground/70",
            )}>
              {step.step}
            </span>
          </div>
        ))}
        {plan.steps.length > 5 ? (
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowAll((prev) => !prev)}
              className="h-6 text-xs px-2"
            >
              {showAll ? "Show less" : `See all ${plan.steps.length} steps`}
            </Button>
            {!showAll && hiddenCount > 0 ? (
              <span className="text-xs text-muted-foreground">+{hiddenCount} more</span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export interface ThinkingIndicatorProps {
  activeToolSummary?: string;
  labels?: string[];
}

function resolvedThinkingLabels(labels?: string[], activeToolSummary?: string): string[] {
  if (labels && labels.length > 0) return labels;
  return thinkingLabelsFromSummary(activeToolSummary);
}

export function ThinkingIndicator({
  activeToolSummary,
  labels,
}: ThinkingIndicatorProps = {}) {
  const [show, setShow] = useState(false);
  const labelOptions = useMemo(
    () => resolvedThinkingLabels(labels, activeToolSummary),
    [labels, activeToolSummary],
  );
  const [labelIndex, setLabelIndex] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setShow(true), 350);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    setLabelIndex(0);
  }, [labelOptions]);

  useEffect(() => {
    if (!show || labelOptions.length < 2) return;
    const interval = window.setInterval(() => {
      setLabelIndex((prev) => (prev + 1) % labelOptions.length);
    }, 1600);
    return () => clearInterval(interval);
  }, [show, labelOptions]);

  if (!show) return null;

  return (
    <div className="px-1" role="status" aria-live="polite" aria-label="Generating response">
      <div className="flex items-center gap-2.5 text-xs text-muted-foreground">
        <span className="size-2 rounded-full bg-ring animate-pulse flex-shrink-0" />
        <span className="font-semibold text-foreground/70">
          {labelOptions[labelIndex] || "Working on it…"}
        </span>
      </div>
    </div>
  );
}

export interface EmptyStateProps {
  onSendMessage: (msg: string) => void;
  suggestions?: EmptyStateSuggestion[];
}

export const DEFAULT_EMPTY_STATE_SUGGESTIONS: EmptyStateSuggestion[] = [
  { text: "Help me get started", icon: "→" },
  { text: "Summarize this for me", icon: "✦" },
  { text: "Help me draft a reply", icon: "✎" },
  { text: "Brainstorm next steps", icon: "⋯" },
];

function LemmaMarkIcon({ className }: { className?: string }) {
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

export function EmptyState({
  onSendMessage,
  suggestions = DEFAULT_EMPTY_STATE_SUGGESTIONS,
}: EmptyStateProps) {

  return (
    <div className="mx-auto flex min-h-[min(28rem,58vh)] w-full max-w-2xl flex-col items-center justify-center gap-6 px-4 py-10 text-center">
      <div className="flex max-w-md flex-col items-center gap-3">
        <Badge variant="secondary" className="size-10 items-center justify-center rounded-lg">
          <LemmaMarkIcon className="size-5 text-foreground" />
        </Badge>
        <h4 className="text-lg font-semibold tracking-tight text-foreground">How can I help?</h4>
        <p className="text-sm leading-6 text-muted-foreground">
          Ask a question, share context, or start with one of these prompts.
        </p>
      </div>

      <div className="grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
        {suggestions.map((suggestion) => (
          <Card
            key={suggestion.text}
            className="min-h-20 cursor-pointer text-left transition-colors hover:border-primary/40 hover:bg-primary/5"
            onClick={() => onSendMessage(suggestion.text)}
          >
            <CardHeader className="p-4">
              <CardDescription className="flex items-start gap-2 text-sm font-medium leading-5 text-foreground/85">
                {suggestion.icon ? <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center text-muted-foreground">{suggestion.icon}</span> : null}
                <span>{suggestion.text}</span>
              </CardDescription>
            </CardHeader>
          </Card>
        ))}
      </div>
    </div>
  );
}

function ReasoningPartCard({
  text,
  isStreaming,
  durationMs,
}: {
  text: string;
  isStreaming: boolean;
  durationMs?: number;
}) {
  return (
    <details className="flex flex-col gap-2" open={isStreaming}>
      <summary className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer list-none">
        <span
          className={cn("font-semibold text-foreground/70", isStreaming && "animate-pulse text-primary")}
        >
          {isStreaming ? "Thinking" : `Thought${durationMs ? ` for ${Math.max(1, Math.round(durationMs / 1000))}s` : ""}`}
        </span>
      </summary>
      <div className="mt-1 pl-4 border-l border-border">
        <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-mono">{text}</pre>
      </div>
    </details>
  );
}

function PresentFilesCard({
  filepaths,
  conversationId,
  renderPresentedFile,
}: {
  filepaths: string[];
  conversationId?: string | null;
  renderPresentedFile?: (args: AssistantPresentedFileRenderArgs) => ReactNode;
}) {
  const fakeMessage: AssistantRenderableMessage = {
    id: "present-files",
    role: "assistant",
    content: "",
  };
  const fakeInvocation: AssistantToolInvocation = {
    toolCallId: "present-files",
    toolName: "present_files",
    args: { filepaths },
    state: "result",
  };

  return (
    <div className="flex flex-col gap-2">
      {filepaths.map((filepath) => (
        <div key={`present-file-${filepath}`}>
          {(renderPresentedFile || defaultPresentedFile)({
            filepath,
            activeConversationId: conversationId ?? null,
            invocation: fakeInvocation,
            message: fakeMessage,
          })}
        </div>
      ))}
    </div>
  );
}

function formatToolDetailValue(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "undefined") return "undefined";

  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length <= 160 ? trimmed : `${trimmed.slice(0, 157)}...`;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    const primitives = value.filter((entry) => (
      typeof entry === "string"
      || typeof entry === "number"
      || typeof entry === "boolean"
      || entry === null
    ));

    if (primitives.length === value.length) {
      const preview = primitives
        .slice(0, 4)
        .map((entry) => (typeof entry === "string" ? `"${entry}"` : String(entry)))
        .join(", ");
      return `[${preview}${value.length > 4 ? ", ..." : ""}]`;
    }

    return `${value.length} item${value.length === 1 ? "" : "s"}`;
  }

  const record = asRecord(value);
  const keys = Object.keys(record);
  if (keys.length === 0) return "{}";
  const preview = keys.slice(0, 4).join(", ");
  return `{ ${preview}${keys.length > 4 ? ", ..." : ""} }`;
}

function humanizeKey(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function isToolPayloadValueMeaningful(value: unknown): boolean {
  if (value === null || typeof value === "undefined") return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(asRecord(value)).length > 0;
  return true;
}

function summarizeToolPayload(
  payload: Record<string, unknown>,
  options?: { excludeKeys?: string[] },
): Array<{ key: string; value: string }> {
  const excluded = new Set((options?.excludeKeys || []).map((key) => key.toLowerCase()));

  return Object.entries(payload)
    .filter(([key, value]) => !excluded.has(key.toLowerCase()) && isToolPayloadValueMeaningful(value))
    .slice(0, 8)
    .map(([key, value]) => ({
      key,
      value: formatToolDetailValue(value),
    }));
}

function countSummarizablePayloadEntries(
  payload: Record<string, unknown>,
  options?: { excludeKeys?: string[] },
): number {
  const excluded = new Set((options?.excludeKeys || []).map((key) => key.toLowerCase()));
  return Object.entries(payload)
    .filter(([key, value]) => !excluded.has(key.toLowerCase()) && isToolPayloadValueMeaningful(value))
    .length;
}

function pickPreferredEntries(
  entries: Array<{ key: string; value: string }>,
  preferredKeys: string[],
  max: number,
): Array<{ key: string; value: string }> {
  const preferredSet = new Set(preferredKeys.map((key) => key.toLowerCase()));
  const preferred = entries.filter((entry) => preferredSet.has(entry.key.toLowerCase()));
  const rest = entries.filter((entry) => !preferredSet.has(entry.key.toLowerCase()));
  return [...preferred, ...rest].slice(0, max);
}

function ToolDetailsPanel({
  toolName,
  args,
  state,
  result,
  onNavigateResource,
  renderToolInvocation,
  message,
  activeConversationId,
}: {
  toolName: string;
  args: ToolCardArgs;
  state: string;
  result?: ToolCardResult;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  message: AssistantRenderableMessage;
  activeConversationId: string | null;
}) {
  const resultData = result || {};
  const primaryLabel = toolCallPrimaryLabel(toolName, args);
  const hasCommentLabel = !!commentLabelFromArgs(args);
  const toolDisplayName = formatToolDisplayName(toolName);
  const canNavigate =
    state === "result"
    && resultData.success !== false
    && typeof resultData.resourceType === "string"
    && typeof resultData.resourceId === "string";
  const summaryOptions = {
    input: { excludeKeys: ["comment", "request", "wait_config"] },
    output: { excludeKeys: ["success", "completed"] },
  };
  const inputEntries = summarizeToolPayload(args, summaryOptions.input);
  const outputEntries = summarizeToolPayload(resultData, summaryOptions.output);
  const inputHighlights = pickPreferredEntries(inputEntries, [
    "cmd",
    "query",
    "path",
    "filepath",
    "filepaths",
    "table",
    "resource_type",
    "resourceType",
    "resource_id",
    "resourceId",
  ], 3);
  const outputHighlights = pickPreferredEntries(outputEntries, [
    "message",
    "stdout",
    "stderr",
    "error",
    "resource_type",
    "resourceType",
    "resource_id",
    "resourceId",
    "exit_code",
    "session_id",
  ], 3);
  const detailRows = [
    {
      label: "Tool",
      value: hasCommentLabel ? `${toolDisplayName} (${toolName})` : toolDisplayName,
    },
    ...inputHighlights.map((entry) => ({
      label: humanizeKey(entry.key),
      value: entry.value,
    })),
    ...outputHighlights.map((entry) => ({
      label: humanizeKey(entry.key),
      value: entry.value,
    })),
  ].slice(0, 8);
  const hiddenInputCount = Math.max(0, countSummarizablePayloadEntries(args, summaryOptions.input) - inputEntries.length);
  const hiddenOutputCount = Math.max(0, countSummarizablePayloadEntries(resultData, summaryOptions.output) - outputEntries.length);

  if (renderToolInvocation) {
    return (
      <div className="mt-2 rounded-lg border border-border/60 bg-card/70 p-3.5 shadow-none">
        {renderToolInvocation({
          invocation: {
            toolCallId: "detail-tool",
            toolName,
            args,
            state: state === "result" ? "result" : "call",
            ...(result ? { result } : {}),
          },
          message,
          activeConversationId,
        })}
      </div>
    );
  }

  return (
    <div className="mt-2 rounded-lg border border-border/60 bg-card/70 p-3.5 shadow-none">
      <div className="mb-3 flex items-start justify-between gap-2 border-b border-border/50 pb-3">
        <div>
          <div className="text-sm font-semibold text-foreground">{primaryLabel}</div>
          {hasCommentLabel ? (
            <div className="text-xs text-muted-foreground">{toolDisplayName}</div>
          ) : null}
        </div>
        {canNavigate && onNavigateResource ? (
            <Button
              type="button"
              variant="link"
              size="sm"
              onClick={() => onNavigateResource?.(resultData.resourceType as string, resultData.resourceId as string)}
              className="text-xs"
            >
              Open
            </Button>
        ) : null}
      </div>
      <dl className="flex flex-col gap-1.5">
        {detailRows.map((row, index) => (
          <div key={`${row.label}-${index}`} className="grid grid-cols-[minmax(96px,auto)_minmax(0,1fr)] items-start gap-2 rounded-md px-2 py-1.5">
            <dt className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">{row.label}</dt>
            <dd className="break-words text-sm leading-relaxed text-foreground">{row.value}</dd>
          </div>
        ))}
      </dl>
      {hiddenInputCount > 0 ? (
        <div className="mt-1 text-xs text-muted-foreground">+{hiddenInputCount} more input field{hiddenInputCount === 1 ? "" : "s"}</div>
      ) : null}
      {hiddenOutputCount > 0 ? (
        <div className="mt-1 text-xs text-muted-foreground">+{hiddenOutputCount} more output field{hiddenOutputCount === 1 ? "" : "s"}</div>
      ) : null}
      <div className="mt-2 flex flex-col gap-1.5">
        <details className="text-xs">
          <summary className="cursor-pointer list-none text-xs text-muted-foreground hover:text-foreground">Raw input JSON</summary>
          <div className="mt-1 overflow-x-auto rounded-md border border-border/50 bg-background/80 p-2">
            <pre className="break-words whitespace-pre-wrap font-mono text-xs text-foreground/80">{JSON.stringify(args, null, 2)}</pre>
          </div>
        </details>
        {Object.keys(resultData).length > 0 ? (
          <details className="text-xs">
            <summary className="cursor-pointer list-none text-xs text-muted-foreground hover:text-foreground">Raw output JSON</summary>
            <div className="mt-1 overflow-x-auto rounded-md border border-border/50 bg-background/80 p-2">
              <pre className="break-words whitespace-pre-wrap font-mono text-xs text-foreground/80">
                {JSON.stringify(resultData, null, 2)}
              </pre>
            </div>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function InlineToolCall({
  invocation,
  isSelected,
  onClick,
  showStem = true,
}: {
  invocation: AssistantToolInvocation;
  isSelected: boolean;
  onClick: () => void;
  showStem?: boolean;
}) {
  const resultData = (invocation.result || {}) as ToolCardResult;
  const isExecuting = invocation.state !== "result";
  const isComplete = invocation.state === "result" && resultData.success !== false;
  const isFailed = invocation.state === "result" && resultData.success === false;
  const primaryLabel = toolCallPrimaryLabel(invocation.toolName, invocation.args);
  const statusLabel = isExecuting ? "Working" : isFailed ? "Failed" : "Done";
  const toolMeta = isExecuting ? `${invocation.toolName} · running` : invocation.toolName;
  const summary = isExecuting
    ? "Running"
    : isFailed
      ? (typeof resultData.error === "string" ? resultData.error : "Tool failed")
      : (formatToolResultSummary(invocation.toolName, invocation.args, resultData) || "Completed");

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "grid w-full grid-cols-[18px_minmax(0,1fr)] gap-2.5 rounded-lg border p-2 text-left text-sm transition-colors",
        isSelected
          ? "border-border/70 bg-background shadow-sm"
          : "border-transparent bg-transparent hover:border-border/50 hover:bg-card/50",
      )}
      data-state={isExecuting ? "executing" : isFailed ? "failed" : "complete"}
      data-selected={isSelected}
    >
      <span className="flex flex-col items-center gap-0.5 pt-0.5" aria-hidden="true">
        <span className={cn(
          "size-2 rounded-full flex-shrink-0",
          isExecuting && "bg-primary animate-pulse",
          isComplete && "bg-green-500",
          isFailed && "bg-destructive",
        )} />
        {showStem ? <span className="w-px flex-1 min-h-3 bg-border" /> : null}
      </span>
      <span className="flex flex-col gap-0.5 min-w-0">
        <span className="flex items-center gap-2">
          <span className="font-medium text-foreground truncate">{primaryLabel}</span>
          <span className={cn(
            "text-xs flex-shrink-0",
            isExecuting && "text-muted-foreground",
            isComplete && "text-green-600",
            isFailed && "text-destructive",
          )}>{statusLabel}</span>
          <span className="text-xs text-muted-foreground/60 flex-shrink-0">{isSelected ? "⌄" : "›"}</span>
        </span>
        <span className="text-xs text-muted-foreground">{toolMeta}</span>
        <span className="text-xs text-muted-foreground/80 truncate">{summary}</span>
      </span>
    </button>
  );
}

function ToolActivityRollup({
  detailParts,
  onNavigateResource,
  renderToolInvocation,
  message,
  activeConversationId,
}: {
  detailParts: Array<
    Extract<AssistantMessagePart, { type: "tool" }>
    | Extract<AssistantMessagePart, { type: "reasoning" }>
  >;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  message: AssistantRenderableMessage;
  activeConversationId: string | null;
}) {
  const [activeToolCallId, setActiveToolCallId] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const toolParts = detailParts.filter((part): part is Extract<AssistantMessagePart, { type: "tool" }> => part.type === "tool");
  const reasoningParts = detailParts.filter((part): part is Extract<AssistantMessagePart, { type: "reasoning" }> => part.type === "reasoning");
  const totalThoughtDurationMs = reasoningParts.reduce((total, part) => total + (part.durationMs ?? 0), 0);
  const shouldCollapse = detailParts.length > 1;

  const activeInvocation = [...toolParts]
    .reverse()
    .find((part) => part.toolInvocation.state !== "result")
    ?.toolInvocation;
  const failedCount = toolParts.filter((part) => (
    part.toolInvocation.state === "result" && part.toolInvocation.result?.success === false
  )).length;
  const isWorking = !!activeInvocation || reasoningParts.some((part) => part.state === "streaming");
  const isSingleDetail = detailParts.length === 1;
  const completionSummary = toolParts.length > 0
    ? `Completed ${toolParts.length} tool${toolParts.length === 1 ? "" : "s"}`
    : totalThoughtDurationMs > 0
      ? `Thought for ${formatDurationCompact(totalThoughtDurationMs)}`
      : "Completed";
  const summary = activeInvocation
    ? formatActiveToolSummary(activeInvocation.toolName, activeInvocation.args)
    : isWorking
      ? "Working on it…"
      : `${completionSummary}${failedCount > 0 ? ` · ${failedCount} failed` : ""}`;
  const collapsedSummary = isWorking
    ? summary
    : `${totalThoughtDurationMs > 0
      ? `Thought for ${formatDurationCompact(totalThoughtDurationMs)}`
      : `Worked through ${detailParts.length} step${detailParts.length === 1 ? "" : "s"}`}${failedCount > 0 ? ` · ${failedCount} failed` : ""}`;

  return (
    <div
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border/60 bg-card/40 p-2.5 shadow-none"
      data-single={isSingleDetail ? "true" : "false"}
    >
      {shouldCollapse ? (
        <button
          type="button"
          className="flex w-full cursor-pointer items-center gap-2.5 rounded-md border border-transparent bg-transparent px-2 py-1.5 text-muted-foreground transition-colors hover:border-border/50 hover:bg-background hover:text-foreground/70"
          onClick={() => setIsExpanded((prev) => !prev)}
          aria-expanded={isExpanded}
          aria-label={isExpanded ? "Hide tool activity details" : "Show tool activity details"}
        >
          <span className="w-px h-4 bg-border" aria-hidden="true" />
          <span className="flex items-center gap-2 text-xs">
            {isWorking ? <span className="size-2 rounded-full bg-primary animate-pulse flex-shrink-0" aria-hidden="true" /> : null}
            <span className={cn(
              "truncate",
              isWorking && "text-primary font-medium",
            )}>
              {collapsedSummary}
            </span>
          </span>
          <span className={cn(
            "text-xs ml-auto flex-shrink-0 transition-transform",
            isExpanded && "rotate-90",
          )} data-expanded={isExpanded}>
            ›
          </span>
        </button>
      ) : (
        !isSingleDetail ? (
          <div className="flex items-center gap-2 px-1 text-xs text-muted-foreground">
            {isWorking ? <span className="size-2 rounded-full bg-primary animate-pulse flex-shrink-0" /> : null}
            <span className={cn(
              "truncate",
              isWorking && "text-primary font-medium",
            )}>{summary}</span>
          </div>
        ) : null
      )}

      {!shouldCollapse || isExpanded ? (
        <div className={cn(
          "flex flex-col gap-1.5",
          isSingleDetail && "gap-0",
        )}>
          {detailParts.map((part, partIndex) => {
            if (part.type === "reasoning") {
              return (
                <div
                  key={`thinking-${part.id}`}
                  className="flex flex-col gap-1"
                >
                  <div className="text-xs font-medium text-muted-foreground">
                    {part.state === "streaming"
                      ? "Internal note"
                      : `Internal note${part.durationMs ? ` · ${formatDurationCompact(part.durationMs)}` : ""}`}
                  </div>
                  <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-mono">
                    {part.text}
                  </pre>
                </div>
              );
            }

            const invocation = part.toolInvocation;
            const isSelected = activeToolCallId === invocation.toolCallId;
            return (
              <div key={part.id}>
                <InlineToolCall
                  invocation={invocation}
                  isSelected={isSelected}
                  showStem={partIndex < detailParts.length - 1}
                  onClick={() => setActiveToolCallId((prev) => (prev === invocation.toolCallId ? null : invocation.toolCallId))}
                />
                {isSelected ? (
                  <ToolDetailsPanel
                    toolName={invocation.toolName}
                    args={invocation.args}
                    state={invocation.state}
                    result={invocation.result}
                    onNavigateResource={onNavigateResource}
                    renderToolInvocation={renderToolInvocation}
                    message={message}
                    activeConversationId={activeConversationId}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function ShowWidgetToolCard({
  invocation,
  onSendPrompt,
}: {
  invocation: AssistantToolInvocation;
  onSendPrompt: (text: string) => void | Promise<void>;
}) {
  const resultData = (invocation.result || {}) as ToolCardResult;
  const payload = extractShowWidgetPayload(invocation.args, resultData);
  const displayName = payload?.title || formatToolDisplayName(invocation.toolName);
  const hasResultData = Object.keys(resultData).length > 0;
  const isExecuting = invocation.state !== "result" && !hasResultData;
  const isFailed = resultData.success === false || (!isExecuting && typeof resultData.error === "string" && resultData.error.length > 0);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [height, setHeight] = useState(220);
  const iframeDocument = useMemo(
    () => (payload ? buildWidgetIframeDocument(invocation.toolCallId, payload) : ""),
    [invocation.toolCallId, payload],
  );

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) return;
      const data = asRecord(event.data);
      const messageType = asString(data.type);
      if (!messageType) return;

      if (messageType === "lemma-widget-send-prompt") {
        const text = asString(data.text);
        if (!text) return;
        void onSendPrompt(text);
        return;
      }

      if (messageType === "lemma-widget-height") {
        const rawHeight = typeof data.height === "number" ? data.height : Number(data.height);
        if (!Number.isFinite(rawHeight)) return;
        setHeight(Math.max(120, Math.min(2400, Math.round(rawHeight))));
      }
    };

    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [onSendPrompt]);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-card/70 p-3.5 shadow-none">
      <div className="flex items-center justify-between gap-2 border-b border-border/50 pb-3">
        <div className="text-sm font-medium text-foreground">{displayName}</div>
        <Badge
          variant={
            isExecuting ? "secondary"
            : isFailed ? "destructive"
            : "outline"
          }
          className={cn(
            "text-xs",
            isExecuting && "animate-pulse",
          )}
        >
          {isExecuting ? "Rendering" : isFailed ? "Failed" : "Ready"}
        </Badge>
      </div>

      {isFailed ? (
        <p className="text-xs text-destructive">
          {typeof resultData.error === "string" && resultData.error.length > 0
            ? resultData.error
            : "Failed to render widget."}
        </p>
      ) : null}

      {!isFailed && payload ? (
        <iframe
          ref={iframeRef}
          title={displayName}
          srcDoc={iframeDocument}
          sandbox="allow-scripts allow-forms allow-popups allow-downloads"
          height={height}
          className="w-full rounded-md border border-border/50 bg-background"
        />
      ) : null}

      {!isFailed && !payload ? (
        <p className="text-xs text-muted-foreground">
          Widget output is missing `widget_code`.
        </p>
      ) : null}
    </div>
  );
}

export function MessageGroup({
  message,
  conversationId,
  assistantLabel = "Agent",
  onNavigateResource,
  onWidgetSendPrompt,
  isStreaming,
  showAssistantHeader,
  renderMessageContent,
  renderPresentedFile,
  renderToolInvocation,
}: {
  message: AssistantRenderableMessage;
  conversationId?: string | null;
  assistantLabel?: ReactNode;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  onWidgetSendPrompt: (text: string) => void | Promise<void>;
  isStreaming: boolean;
  showAssistantHeader: boolean;
  renderMessageContent: (args: AssistantMessageRenderArgs) => ReactNode;
  renderPresentedFile?: (args: AssistantPresentedFileRenderArgs) => ReactNode;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
}) {
  type ToolPart = Extract<AssistantMessagePart, { type: "tool" }>;
  type ReasoningPart = Extract<AssistantMessagePart, { type: "reasoning" }>;
  type NonToolPart = Exclude<AssistantMessagePart, { type: "tool" }>;
  type MessageRenderBlock =
    | { id: string; kind: "content"; part: NonToolPart }
    | { id: string; kind: "widget"; toolPart: ToolPart }
    | { id: string; kind: "tools"; toolParts: ToolPart[] };

  const orderedParts: AssistantMessagePart[] = message.parts && message.parts.length > 0
    ? message.parts
    : [
      ...(message.content?.trim()
        ? [{ id: `${message.id}-fallback-text`, type: "text", text: message.content } as AssistantMessagePart]
        : []),
      ...((message.toolInvocations || []).map((tool, index) => ({
        id: `${tool.toolCallId || message.id}-fallback-tool-${index}`,
        type: "tool",
        toolInvocation: tool,
      } as AssistantMessagePart))),
    ];

  const toolParts = orderedParts.filter((part): part is ToolPart => part.type === "tool");
  const groupedToolParts = toolParts.filter((part) => !isShowWidgetToolName(part.toolInvocation.toolName));
  const reasoningParts = orderedParts.filter((part): part is ReasoningPart => part.type === "reasoning");
  const presentableFilepaths = Array.from(new Set(
    groupedToolParts.flatMap((part) => extractPresentFilePathsFromInvocation(part.toolInvocation)),
  ));
  const rollupOrderedParts = orderedParts.filter((part): part is ToolPart | ReasoningPart => (
    part.type === "reasoning" || (part.type === "tool" && !isShowWidgetToolName(part.toolInvocation.toolName))
  ));
  const blocks: MessageRenderBlock[] = [];

  orderedParts.forEach((part) => {
    if (part.type === "tool") {
      if (isShowWidgetToolName(part.toolInvocation.toolName)) {
        blocks.push({
          id: `${part.id}-widget`,
          kind: "widget",
          toolPart: part,
        });
        return;
      }

      const lastBlock = blocks[blocks.length - 1];
      if (lastBlock?.kind === "tools") {
        lastBlock.toolParts.push(part);
      } else {
        blocks.push({
          id: `${part.id}-tools`,
          kind: "tools",
          toolParts: [part],
        });
      }
      return;
    }

    blocks.push({
      id: part.id,
      kind: "content",
      part,
    });
  });

  const nonToolParts = orderedParts.filter((part): part is NonToolPart => part.type !== "tool");
  const firstToolsBlockId = blocks.find((block) => block.kind === "tools")?.id;
  const hasTextParts = orderedParts.some((part) => part.type === "text" && part.text.trim().length > 0);
  const foldReasoningIntoToolRollup = groupedToolParts.length > 0 && reasoningParts.length > 0 && !hasTextParts;
  const lastTextPartId = [...nonToolParts]
    .reverse()
    .find((part) => part.type === "text" && part.text.trim().length > 0)
    ?.id;
  const messageTimestamp = formatMessageTimestamp(message.createdAt);

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="flex flex-col items-end gap-1">
          <div className="ml-auto max-w-prose rounded-lg bg-primary px-3 py-2.5 text-primary-foreground">
            {renderMessageContent({
              message: {
                ...message,
                content: message.content,
                parts: undefined,
                toolInvocations: undefined,
              },
            })}
          </div>
          {messageTimestamp ? (
            <time
              className="text-xs text-muted-foreground"
              dateTime={messageTimestamp.dateTime}
            >
              {messageTimestamp.text}
            </time>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      {showAssistantHeader ? (
        <Badge variant="outline" className="w-fit gap-1.5 border-border/60 bg-background px-1.5 py-0 text-xs font-normal text-muted-foreground">
          <span className="size-1.5 rounded-full bg-primary/40" />
          {assistantLabel}
        </Badge>
      ) : null}

      <div className="flex flex-col gap-1">
        {blocks.map((block) => {
          if (block.kind === "tools") {
            if (foldReasoningIntoToolRollup && block.id !== firstToolsBlockId) {
              return null;
            }

            return (
              <ToolActivityRollup
                key={block.id}
                detailParts={
                  foldReasoningIntoToolRollup && block.id === firstToolsBlockId
                    ? rollupOrderedParts
                    : block.toolParts
                }
                onNavigateResource={onNavigateResource}
                renderToolInvocation={renderToolInvocation}
                message={message}
                activeConversationId={conversationId ?? null}
              />
            );
          }

          if (block.kind === "widget") {
            return (
              <ShowWidgetToolCard
                key={block.id}
                invocation={block.toolPart.toolInvocation}
                onSendPrompt={onWidgetSendPrompt}
              />
            );
          }

          const part = block.part;
          if (part.type === "text") {
            const trimmedText = part.text.trim();
            if (trimmedText.length === 0) {
              return null;
            }

            return (
              <div key={part.id}>
                {renderMessageContent({
                  message: {
                    ...message,
                    content: trimmedText,
                    parts: undefined,
                    toolInvocations: undefined,
                  },
                })}
                {isStreaming && part.id === lastTextPartId ? (
                  <span className="inline animate-pulse text-primary">▍</span>
                ) : null}
              </div>
            );
          }

          if (part.type === "reasoning") {
            if (foldReasoningIntoToolRollup) {
              return null;
            }

            return (
              <ReasoningPartCard
                key={part.id}
                text={part.text}
                isStreaming={part.state === "streaming"}
                durationMs={part.durationMs}
              />
            );
          }

          return null;
        })}

        {presentableFilepaths.length > 0 ? (
          <PresentFilesCard
            filepaths={presentableFilepaths}
            conversationId={conversationId}
            renderPresentedFile={renderPresentedFile}
          />
        ) : null}
      </div>
    </div>
  );
}

export function AssistantExperienceView({
  controller,
  title = "Lemma Agent",
  subtitle = "Ask across your workspace and organization.",
  badge,
  className,
  placeholder = "Message Lemma Agent",
  emptyState,
  emptyStateSuggestions,
  launchContext,
  popupOpen: controlledPopupOpen,
  defaultPopupOpen = false,
  onPopupOpenChange,
  popupPosition = "bottom-right",
  popupTriggerLabel,
  popupTriggerIcon,
  popupTriggerVariant = "icon",
  draft: controlledDraft,
  onDraftChange,
  showConversationList = false,
  mode = "page",
  appearance = "default",
  density = "comfortable",
  chromeStyle,
  statusPlacement = "inline",
  radius = "lg",
  showModelPicker = false,
  showNewConversationButton = true,
  onNavigateResource,
  renderConversationLabel = defaultConversationLabel,
  renderMessageContent = defaultMessageContent,
  renderPresentedFile,
  renderPendingFile = defaultPendingFile,
  renderToolInvocation,
}: AssistantExperienceViewProps) {
  const [draft, setDraft] = useControllableDraft(controlledDraft, onDraftChange);
  const [uncontrolledPopupOpen, setUncontrolledPopupOpen] = useState(defaultPopupOpen);
  const [isPlanHidden, setIsPlanHidden] = useState(false);
  const [dismissedAskToolCallIds, setDismissedAskToolCallIds] = useState<string[]>([]);
  const [askOverlayState, setAskOverlayState] = useState<{
    toolCallId: string;
    currentQuestionIndex: number;
    answers: string[][];
  } | null>(null);
  const [isUpdatingModel, setIsUpdatingModel] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [thinkingLabelIndex, setThinkingLabelIndex] = useState(0);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const bottomAnchorRef = useRef<HTMLDivElement>(null);
  const askOverlayRef = useRef<HTMLDivElement>(null);
  const isPinnedToBottomRef = useRef(true);
  const loadingOlderFromScrollRef = useRef(false);
  const isConversationBusy = controller.isLoading || controller.isActiveConversationRunning;
  const resolvedChromeStyle = chromeStyle ?? assistantChromeStyleFromAppearance(appearance);
  const controllerMessages = controller.messages;
  const activeConversationId = controller.activeConversationId;
  const hasOlderMessages = controller.hasOlderMessages;
  const isLoadingMessages = controller.isLoadingMessages;
  const isLoadingOlderMessages = controller.isLoadingOlderMessages;
  const sendMessage = controller.sendMessage;
  const uploadFiles = controller.uploadFiles;
  const loadOlderMessages = controller.loadOlderMessages;
  const setConversationModel = controller.setConversationModel;
  const isPopupMode = mode === "popup";
  const resolvedPopupOpen = controlledPopupOpen ?? uncontrolledPopupOpen;
  const surfaceMode: LemmaAssistantMode = isPopupMode ? "popup" : mode;

  const availableModels = useMemo(
    () => {
      const dynamicModels = controller.availableModels
        .map((model) => model.id as ConversationModel)
        .filter((model) => model.trim().length > 0);
      return dynamicModels.length > 0
        ? dynamicModels
        : [];
    },
    [controller.availableModels],
  );
  const availableModelLabels = useMemo(
    () => new Map(controller.availableModels.map((model) => [model.id, model.name])),
    [controller.availableModels],
  );
  const launchContextItems = useMemo(() => normalizeLaunchContext(launchContext), [launchContext]);
  const resolvedPopupTriggerLabel = popupTriggerLabel || `Open ${plainLabelFromNode(title) || "agent"}`;

  const handlePopupOpenChange = useCallback((open: boolean) => {
    if (controlledPopupOpen === undefined) {
      setUncontrolledPopupOpen(open);
    }
    onPopupOpenChange?.(open);
  }, [controlledPopupOpen, onPopupOpenChange]);

  const resizeComposer = useCallback(() => {
    const textarea = inputRef.current;
    if (!textarea) return;

    const minHeight = 48;
    const maxHeight = 220;

    const currentHeight = textarea.offsetHeight;
    textarea.style.height = `${minHeight}px`;
    const nextHeight = Math.min(maxHeight, Math.max(minHeight, textarea.scrollHeight));
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, []);

  const scrollToLatest = useCallback((behavior: ScrollBehavior = "auto") => {
    const anchor = bottomAnchorRef.current;
    if (anchor) {
      anchor.scrollIntoView({
        block: "end",
        behavior,
      });
      isPinnedToBottomRef.current = true;
      setShowScrollToBottom(false);
      return;
    }

    const el = messagesContainerRef.current;
    if (!el) return;
    el.scrollTo({
      top: el.scrollHeight,
      behavior,
    });
    isPinnedToBottomRef.current = true;
    setShowScrollToBottom(false);
  }, []);

  const updatePinnedState = useCallback(() => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const isPinned = distanceFromBottom <= 112;
    isPinnedToBottomRef.current = isPinned;
    setShowScrollToBottom((prev) => (prev === !isPinned ? prev : !isPinned));

    if (el.scrollTop > 48) return;
    if (!hasOlderMessages || isLoadingMessages || isLoadingOlderMessages || loadingOlderFromScrollRef.current) return;

    const previousScrollTop = el.scrollTop;
    const previousScrollHeight = el.scrollHeight;
    loadingOlderFromScrollRef.current = true;

    void loadOlderMessages()
      .then((didLoad) => {
        if (!didLoad) return;
        requestAnimationFrame(() => {
          const nextEl = messagesContainerRef.current;
          if (!nextEl) return;
          nextEl.scrollTop = previousScrollTop + (nextEl.scrollHeight - previousScrollHeight);
        });
      })
      .finally(() => {
        loadingOlderFromScrollRef.current = false;
      });
  }, [hasOlderMessages, isLoadingMessages, isLoadingOlderMessages, loadOlderMessages]);

  useEffect(() => {
    const el = messagesContainerRef.current;
    if (!el) return;

    if (isPinnedToBottomRef.current) {
      if (isConversationBusy) {
        scrollToLatest("auto");
      } else {
        requestAnimationFrame(() => {
          scrollToLatest("smooth");
        });
      }
    }
  }, [controllerMessages, isConversationBusy, scrollToLatest]);

  useEffect(() => {
    isPinnedToBottomRef.current = true;
    setShowScrollToBottom(false);
    requestAnimationFrame(() => {
      scrollToLatest("auto");
      inputRef.current?.focus();
    });
  }, [activeConversationId, scrollToLatest]);

  useEffect(() => {
    if (!isPopupMode || !resolvedPopupOpen) return;
    requestAnimationFrame(() => {
      scrollToLatest("auto");
      inputRef.current?.focus();
    });
  }, [isPopupMode, resolvedPopupOpen, scrollToLatest]);

  useEffect(() => {
    resizeComposer();
  }, [draft, resizeComposer]);

  const displayMessageRows = useMemo(() => buildDisplayMessageRows(controllerMessages), [controllerMessages]);
  const activeToolBanner = useMemo(() => getActiveToolBanner(controllerMessages), [controllerMessages]);
  const thinkingLabels = useMemo(
    () => thinkingLabelsFromSummary(activeToolBanner?.summary),
    [activeToolBanner?.summary],
  );
  const planSummary = useMemo(() => latestPlanSummary(controllerMessages), [controllerMessages]);
  const pendingAskUserInput = useMemo(() => {
    const pending = findPendingAskUserInput(controllerMessages);
    if (!pending) return null;
    if (dismissedAskToolCallIds.includes(pending.toolCallId)) return null;
    return pending;
  }, [controllerMessages, dismissedAskToolCallIds]);
  const effectiveAskOverlayState = useMemo(() => {
    if (!pendingAskUserInput) return null;
    if (askOverlayState && askOverlayState.toolCallId === pendingAskUserInput.toolCallId) {
      return askOverlayState;
    }
    return {
      toolCallId: pendingAskUserInput.toolCallId,
      currentQuestionIndex: 0,
      answers: pendingAskUserInput.questions.map(() => []),
    };
  }, [askOverlayState, pendingAskUserInput]);

  const lastMessageHasContent = useMemo(() => {
    if (controllerMessages.length === 0) return false;
    const lastMsg = controllerMessages[controllerMessages.length - 1];
    if (lastMsg.role !== "assistant") return false;
    const hasText = (lastMsg.parts || []).some((part) => part.type === "text" && part.text.trim().length > 0);
    const hasTools = (lastMsg.toolInvocations?.length || 0) > 0 || (lastMsg.parts || []).some((part) => part.type === "tool");
    return hasText || hasTools;
  }, [controllerMessages]);

  useEffect(() => {
    setThinkingLabelIndex(0);
  }, [activeToolBanner?.summary, isConversationBusy]);

  useEffect(() => {
    if (!isConversationBusy || thinkingLabels.length < 2) return;

    const interval = window.setInterval(() => {
      setThinkingLabelIndex((prev) => (prev + 1) % thinkingLabels.length);
    }, 1700);

    return () => clearInterval(interval);
  }, [isConversationBusy, thinkingLabels]);

  const dismissAskOverlay = useCallback((toolCallId: string) => {
    setDismissedAskToolCallIds((prev) => (prev.includes(toolCallId) ? prev : [...prev, toolCallId]));
    setAskOverlayState(null);
  }, []);

  const commitAskAnswersToComposer = useCallback((toolCallId: string, answers: string[][]) => {
    if (!pendingAskUserInput || pendingAskUserInput.toolCallId !== toolCallId) return;
    const formatted = formatAskUserInputAnswers(pendingAskUserInput.questions, answers);
    if (formatted.length > 0) {
      const nextDraft = draft.trim().length > 0 ? `${draft.trim()}\n\n${formatted}` : formatted;
      setDraft(nextDraft);
    }
    dismissAskOverlay(toolCallId);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
  }, [dismissAskOverlay, draft, pendingAskUserInput, setDraft]);

  const updateAskAnswer = useCallback((option: string) => {
    if (!pendingAskUserInput || !effectiveAskOverlayState) return;
    if (effectiveAskOverlayState.toolCallId !== pendingAskUserInput.toolCallId) return;

    const questionIndex = effectiveAskOverlayState.currentQuestionIndex;
    const question = pendingAskUserInput.questions[questionIndex];
    if (!question) return;

    const nextAnswers = effectiveAskOverlayState.answers.map((answers) => [...answers]);
    const currentAnswers = nextAnswers[questionIndex] || [];

    if (question.type === "single_select") {
      nextAnswers[questionIndex] = [option];
      if (questionIndex >= pendingAskUserInput.questions.length - 1) {
        commitAskAnswersToComposer(effectiveAskOverlayState.toolCallId, nextAnswers);
        return;
      }
      setAskOverlayState({
        ...effectiveAskOverlayState,
        answers: nextAnswers,
        currentQuestionIndex: questionIndex + 1,
      });
      return;
    }

    nextAnswers[questionIndex] = currentAnswers.includes(option)
      ? currentAnswers.filter((entry) => entry !== option)
      : [...currentAnswers, option];

    setAskOverlayState({
      ...effectiveAskOverlayState,
      answers: nextAnswers,
    });
  }, [commitAskAnswersToComposer, effectiveAskOverlayState, pendingAskUserInput]);

  const continueAskQuestions = useCallback(() => {
    if (!pendingAskUserInput || !effectiveAskOverlayState) return;
    if (effectiveAskOverlayState.toolCallId !== pendingAskUserInput.toolCallId) return;

    const questionIndex = effectiveAskOverlayState.currentQuestionIndex;
    const answers = effectiveAskOverlayState.answers[questionIndex] || [];
    if (answers.length === 0) return;

    if (questionIndex >= pendingAskUserInput.questions.length - 1) {
      commitAskAnswersToComposer(effectiveAskOverlayState.toolCallId, effectiveAskOverlayState.answers);
      return;
    }

    setAskOverlayState({
      ...effectiveAskOverlayState,
      currentQuestionIndex: questionIndex + 1,
    });
  }, [commitAskAnswersToComposer, effectiveAskOverlayState, pendingAskUserInput]);

  const handleSubmit = useCallback(async () => {
    if (!draft.trim() || isConversationBusy) return;
    const message = draft.trim();
    setDraft("");
    scrollToLatest("smooth");
    await sendMessage(message);
  }, [draft, isConversationBusy, scrollToLatest, sendMessage, setDraft]);

  const handleWidgetSendPrompt = useCallback(async (prompt: string) => {
    const message = prompt.trim();
    if (!message) return;

    if (isConversationBusy) {
      setDraft(message);
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
      return;
    }

    scrollToLatest("smooth");
    await sendMessage(message);
  }, [isConversationBusy, scrollToLatest, sendMessage, setDraft]);

  const handleSuggestionSend = useCallback(async (suggestion: string) => {
    const message = suggestion.trim();
    if (!message || isConversationBusy) return;
    scrollToLatest("smooth");
    await sendMessage(message);
  }, [isConversationBusy, scrollToLatest, sendMessage]);

  const handleLaunchContextPrompt = useCallback(async (item: AssistantLaunchContextItem) => {
    const nextPrompt = (item.prompt || defaultLaunchContextPrompt(item)).trim();
    if (!nextPrompt) return;
    if (isConversationBusy) {
      setDraft(nextPrompt);
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
      return;
    }
    scrollToLatest("smooth");
    await sendMessage(nextPrompt);
  }, [isConversationBusy, scrollToLatest, sendMessage, setDraft]);

  const handleUploadSelection = useCallback(async (files: FileList | null) => {
    const selectedFiles = files ? Array.from(files) : [];
    if (selectedFiles.length === 0) return;

    try {
      await uploadFiles(selectedFiles, { deferUntilSend: true });
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }, [uploadFiles]);

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  }, [handleSubmit]);

  const handleModelChange = useCallback(async (nextModel: string | null) => {
    if (isUpdatingModel) return;
    setIsUpdatingModel(true);
    try {
      await setConversationModel(nextModel);
    } finally {
      setIsUpdatingModel(false);
    }
  }, [isUpdatingModel, setConversationModel]);

  const activeAskQuestion = pendingAskUserInput
    && effectiveAskOverlayState
    && effectiveAskOverlayState.toolCallId === pendingAskUserInput.toolCallId
    ? pendingAskUserInput.questions[effectiveAskOverlayState.currentQuestionIndex]
    : null;
  const activeAskAnswers = activeAskQuestion && effectiveAskOverlayState
    ? effectiveAskOverlayState.answers[effectiveAskOverlayState.currentQuestionIndex] || []
    : [];
  const canContinueAsk = activeAskAnswers.length > 0;

  useEffect(() => {
    if (!activeAskQuestion || !askOverlayRef.current) return;
    const firstButton = askOverlayRef.current.querySelector<HTMLButtonElement>("button");
    firstButton?.focus();
  }, [activeAskQuestion]);

  const liveStatusLabel = thinkingLabels[thinkingLabelIndex] || "Working on it…";
  const headerTone: AssistantSurfaceTone = resolvedChromeStyle === "elevated" ? "default" : resolvedChromeStyle === "flat" ? "flat" : "subtle";
  const composerTone: AssistantSurfaceTone = resolvedChromeStyle === "flat" ? "flat" : resolvedChromeStyle === "subtle" ? "subtle" : "default";
  const showInlineStatus = statusPlacement === "inline" && isConversationBusy;
  const showComposerStatus = statusPlacement === "composer" && isConversationBusy;
  const resolvedHeaderBadge = badge === undefined
    ? <LemmaMarkIcon className="size-4.5 text-primary-foreground" />
    : badge;

  const assistantSurface = (
    <div
      className={cn(assistantRootClassName(surfaceMode, appearance, radius, showConversationList), className)}
      data-appearance={appearance}
      data-density={density}
      data-mode={surfaceMode}
      data-chrome-style={resolvedChromeStyle}
      data-status-placement={statusPlacement}
      data-radius={radius}
      data-show-model-picker={showModelPicker ? "true" : "false"}
      data-busy={isConversationBusy ? "true" : "false"}
      data-has-plan={planSummary ? "true" : "false"}
      data-has-pending-files={controller.pendingFiles.length > 0 ? "true" : "false"}
      data-show-conversation-list={showConversationList ? "true" : "false"}
    >
      {showConversationList ? (
        <aside className={assistantSidebarClassName(appearance)}>
          <div className="border-b border-border/60 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">Conversations</div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {controller.conversations.length} total
                </div>
              </div>
              {showNewConversationButton ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={controller.clearMessages}
                  className="h-8 px-3 text-sm"
                >
                  New
                </Button>
              ) : null}
            </div>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
            {controller.conversations.map((conversation) => {
              const isActive = conversation.id === controller.activeConversationId;
              return (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => controller.selectConversation(conversation.id)}
                  aria-selected={isActive}
                  className={cn(
                     "w-full border px-3 py-3 text-left text-sm transition-colors",
                     assistantRadiusClassName(radius, "item"),
                     isActive
                       ? "border-border bg-background shadow-sm"
                       : "border-transparent bg-transparent text-foreground/80 hover:border-border/50 hover:bg-background/70",
                   )}
                >
                  <div className="truncate font-medium">
                    {renderConversationLabel({ conversation, isActive })}
                  </div>
                  <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <span className={cn("size-1.5 rounded-full flex-shrink-0", conversationStatusDotColor(conversation.status))} />
                    <span>{relativeTimeAgo(conversation.updated_at || conversation.created_at)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </aside>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
          <AssistantHeader
            tone={headerTone}
            title={title}
            subtitle={subtitle}
            badge={resolvedHeaderBadge}
            controls={showModelPicker || showNewConversationButton ? (
              <>
                {showModelPicker ? (
                  <AssistantModelPicker
                    value={controller.conversationModel}
                    options={availableModels}
                    getOptionLabel={(model) => availableModelLabels.get(model) ?? model}
                    onChange={(nextModel) => { void handleModelChange(nextModel); }}
                    disabled={isConversationBusy || isUpdatingModel}
                    autoLabel="Auto"
                  />
                ) : null}
                {showNewConversationButton ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={controller.clearMessages}
                    title="New conversation"
                    className="size-9"
                  >
                    <RotateCcw className="size-3.5" />
                  </Button>
                ) : null}
              </>
            ) : undefined}
          />

          {launchContextItems.length > 0 ? (
            <div className={cn(
              "border-b border-border/60 bg-muted/10",
              surfaceMode === "embedded" || surfaceMode === "popup" ? "px-4 py-3" : "px-4 py-3 sm:px-6",
            )}>
              <div className="flex flex-wrap gap-3">
                {launchContextItems.map((item, index) => {
                  const actionLabel = item.actionLabel ?? "Ask";
                  const promptText = item.prompt || defaultLaunchContextPrompt(item);
                  const card = (
                    <Card className={cn(
                      "border-border/60 bg-card/70 shadow-none",
                      surfaceMode === "side-panel" ? "w-full" : "min-w-[16rem] flex-1",
                    )}>
                      <CardHeader className="gap-3 border-b border-border/50 pb-3">
                        <CardDescription className="flex items-center gap-2 text-xs uppercase tracking-widest text-muted-foreground">
                          <span className="flex size-7 items-center justify-center rounded-md border border-border/50 bg-background text-foreground/70">
                            {launchContextIcon(item.kind)}
                          </span>
                          <span>{launchContextKindLabel(item.kind)}</span>
                        </CardDescription>
                        <CardTitle className="text-sm">{item.title}</CardTitle>
                        {item.description ? (
                          <CardDescription className="text-xs leading-5">{item.description}</CardDescription>
                        ) : null}
                      </CardHeader>
                      {(item.meta || item.prompt || item.onSelect || item.href) ? (
                        <CardContent className="flex flex-wrap items-center gap-2 pt-3">
                          {item.meta ? (
                            <Badge variant="secondary" className="max-w-full truncate border border-border/50 bg-background text-muted-foreground">{item.meta}</Badge>
                          ) : null}
                          {promptText ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => { void handleLaunchContextPrompt(item); }}
                            >
                              {actionLabel}
                            </Button>
                          ) : null}
                          {item.onSelect ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={item.onSelect}
                            >
                              Open
                            </Button>
                          ) : item.href ? (
                            <a
                              href={item.href}
                              className="inline-flex h-7 items-center justify-center rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                            >
                              Open
                            </a>
                          ) : null}
                        </CardContent>
                      ) : null}
                    </Card>
                  );
                  return <div key={`${item.kind}-${index}`} className={cn(surfaceMode === "side-panel" ? "w-full" : "flex min-w-[16rem] flex-1")}>{card}</div>;
                })}
              </div>
            </div>
          ) : null}

          <AssistantMessageViewport
            ref={messagesContainerRef}
            onScroll={updatePinnedState}
            className={cn(
              (surfaceMode === "embedded" || surfaceMode === "popup") && "px-4 py-4 sm:px-4",
              surfaceMode === "side-panel" && "px-4 py-4 sm:px-5 lg:px-5",
            )}
            innerClassName={cn(
              surfaceMode === "side-panel" && "max-w-none",
              (surfaceMode === "embedded" || surfaceMode === "popup") && "max-w-none",
            )}
          >
            <div className="flex w-full flex-col gap-5" aria-live="polite" aria-atomic="false">
            {controller.messages.length === 0 && !isConversationBusy ? (
              emptyState || (
                <EmptyState
                  onSendMessage={(message) => { void handleSuggestionSend(message); }}
                  suggestions={emptyStateSuggestions}
                />
              )
            ) : null}

            {(controller.isLoadingMessages && controller.messages.length === 0) ? (
              <div className="flex items-center justify-center py-8">
                <span className="text-sm text-muted-foreground">Loading…</span>
              </div>
            ) : null}

            {(controller.isLoadingOlderMessages && controller.messages.length > 0) ? (
              <div className="flex items-center justify-center py-2">
                <span className="text-xs text-muted-foreground">Loading older…</span>
              </div>
            ) : null}

            {displayMessageRows.map((row, index) => {
              const previousRow = index > 0 ? displayMessageRows[index - 1] : null;
              const showAssistantHeader =
                row.message.role !== "assistant"
                  ? false
                  : previousRow?.message.role !== "assistant";
              const includesLastRawMessage = row.sourceIndexes.includes(controller.messages.length - 1);
              const compactAfterAssistant = row.message.role === "assistant" && previousRow?.message.role === "assistant";

              return (
                <div key={row.id || index} className={cn(compactAfterAssistant && "-mt-3")}>
                  <MessageGroup
                    message={row.message}
                    assistantLabel={title}
                    onNavigateResource={onNavigateResource}
                    onWidgetSendPrompt={handleWidgetSendPrompt}
                    conversationId={controller.activeConversationId}
                    isStreaming={isConversationBusy && includesLastRawMessage && row.message.role === "assistant"}
                    showAssistantHeader={showAssistantHeader}
                    renderMessageContent={renderMessageContent}
                    renderPresentedFile={renderPresentedFile}
                    renderToolInvocation={renderToolInvocation}
                  />
                </div>
              );
            })}

            {showInlineStatus ? (
              <div className="flex items-center gap-2">
                <div
                  data-has-content={lastMessageHasContent ? "true" : "false"}
                >
                  <AssistantStatusPill
                    label={liveStatusLabel}
                    subtle={lastMessageHasContent}
                  />
                </div>
              </div>
            ) : null}

            {controller.error ? (
              <div className="flex items-start gap-2.5 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                <div>
                  <p className="font-semibold">Something went wrong</p>
                  <p className="mt-1">
                    {controller.error instanceof Error ? controller.error.message : String(controller.error)}
                  </p>
                </div>
              </div>
            ) : null}

            {showScrollToBottom ? (
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={() => scrollToLatest("smooth")}
              className="sticky bottom-2 z-10 ml-auto size-8 shadow-md"
              aria-label="Scroll to latest messages"
            >
              ↓
            </Button>
            ) : null}
            {(controller.messages.length > 0 || isConversationBusy || !!controller.error) ? (
              <div aria-hidden="true" className="h-2" />
            ) : null}
            <div ref={bottomAnchorRef} aria-hidden="true" className="h-px" />
            </div>
          </AssistantMessageViewport>
        </div>

        <AssistantComposer
          tone={composerTone}
          radius={radius}
          floating={planSummary ? (
            isPlanHidden ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setIsPlanHidden(false)}
                className="h-7 px-2 text-xs"
              >
                Show plan ({controller.completedActions.length}/{controller.completedActions.length + controller.pendingActions.length})
              </Button>
            ) : (
              <PlanSummaryStrip
                plan={planSummary}
                onHide={() => setIsPlanHidden(true)}
              />
            )
          ) : undefined}
          status={showComposerStatus ? (
            <AssistantStatusPill label={liveStatusLabel} subtle />
          ) : undefined}
          pendingFiles={controller.pendingFiles.length > 0 ? (
            <>
              {controller.pendingFiles.map((file) => {
                const fileKey = `${file.name}:${file.size}:${file.lastModified}`;
                return (
                  <div key={fileKey}>
                    {renderPendingFile({
                      file,
                      remove: () => controller.removePendingFile(fileKey),
                    })}
                  </div>
                );
              })}
            </>
          ) : undefined}
        >
          {activeAskQuestion && effectiveAskOverlayState && pendingAskUserInput ? (
            <div ref={askOverlayRef}>
            <AssistantAskOverlay
              questionNumber={effectiveAskOverlayState.currentQuestionIndex + 1}
              totalQuestions={pendingAskUserInput.questions.length}
              question={activeAskQuestion.question}
              options={activeAskQuestion.options}
              selectedOptions={activeAskAnswers}
              canContinue={canContinueAsk}
              continueLabel={effectiveAskOverlayState.currentQuestionIndex >= pendingAskUserInput.questions.length - 1 ? "Use answers" : "Continue"}
              onSelectOption={updateAskAnswer}
              onContinue={activeAskQuestion.type !== "single_select" || pendingAskUserInput.questions.length > 1 ? continueAskQuestions : undefined}
              onSkip={() => dismissAskOverlay(effectiveAskOverlayState.toolCallId)}
              mode={activeAskQuestion.type}
              radius={radius}
            />
            </div>
          ) : (
            <div className="min-w-0">
              <div className={assistantComposerInputClassName(radius)}>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => { void handleUploadSelection(event.target.files); }}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => fileInputRef.current?.click()}
                disabled={isConversationBusy || controller.isUploadingFiles}
                className="size-9 shrink-0"
                data-disabled={isConversationBusy || controller.isUploadingFiles ? "true" : "false"}
                title="Upload files"
              >
                <Plus className="size-4" />
              </Button>
              <Textarea
                ref={inputRef}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                className="max-h-[220px] min-h-10 flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm leading-6 shadow-none focus-visible:border-0 focus-visible:ring-0"
                rows={1}
                disabled={isConversationBusy}
              />
              <Button
                type="button"
                variant={isConversationBusy ? "destructive" : draft.trim() ? "default" : "ghost"}
                size="icon"
                onClick={isConversationBusy ? controller.stop : () => { void handleSubmit(); }}
                disabled={!isConversationBusy && !draft.trim()}
                className="size-9 shrink-0"
                data-state={isConversationBusy ? "busy" : draft.trim() ? "ready" : "idle"}
                aria-label={isConversationBusy ? "Stop generating" : "Send message"}
                title={isConversationBusy ? "Stop generating" : "Send message"}
              >
                {isConversationBusy ? <Square className="size-3" /> : <ArrowUp className="size-4" />}
              </Button>
              </div>
            </div>
          )}
        </AssistantComposer>
      </div>
    </div>
  );

  if (!isPopupMode) {
    return assistantSurface;
  }

  return (
    <>
      <div className={cn("fixed z-40", assistantPopupPositionClassName(popupPosition))}>
        <Button
          type="button"
          size={popupTriggerVariant === "pill" ? "sm" : "icon"}
          onClick={() => handlePopupOpenChange(true)}
          className={cn(
            "border border-border/60 bg-primary text-primary-foreground shadow-lg shadow-primary/20 transition-transform hover:scale-[1.02] hover:bg-primary/90",
            popupTriggerVariant === "pill"
              ? "h-12 rounded-full px-4 text-sm font-medium"
              : "size-14 rounded-full",
          )}
          aria-label={resolvedPopupTriggerLabel}
          title={resolvedPopupTriggerLabel}
        >
          {popupTriggerIcon ?? <LemmaMarkIcon className={cn(popupTriggerVariant === "pill" ? "size-4.5" : "size-5")} />}
          {popupTriggerVariant === "pill" ? (
            <span className="ml-2 truncate">{resolvedPopupTriggerLabel}</span>
          ) : (
            <span className="sr-only">{resolvedPopupTriggerLabel}</span>
          )}
        </Button>
      </div>

      <Dialog open={resolvedPopupOpen} onOpenChange={handlePopupOpenChange}>
        <DialogContent showCloseButton={false} className="overflow-hidden border-0 bg-transparent p-0 shadow-none sm:max-w-5xl">
          <div className="sr-only">
            <DialogTitle>{plainLabelFromNode(title) || "Agent"}</DialogTitle>
            <DialogDescription>{plainLabelFromNode(subtitle) || "Agent workspace"}</DialogDescription>
          </div>
          <div className="relative">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => handlePopupOpenChange(false)}
              className="absolute right-3 top-3 z-10 size-8 rounded-full border border-border/60 bg-background/90 text-muted-foreground shadow-sm hover:bg-background hover:text-foreground"
              aria-label="Close agent"
            >
              <X className="size-4" />
            </Button>
            {assistantSurface}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
