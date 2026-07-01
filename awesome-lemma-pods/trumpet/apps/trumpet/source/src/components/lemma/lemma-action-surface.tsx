"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { LemmaClient, type Conversation, type ConversationMessage, type FlowRun, type FunctionRun } from "lemma-sdk";
import { useConversationMessages, useFunctionRun, useWorkflowStart } from "lemma-sdk/react";
import { AlertCircle, Bot, CheckCircle2, ChevronRight, Loader2, Play, Sparkles, Workflow, XCircle } from "lucide-react";
import { cn } from "@/components/lemma/lib/utils";
import { Badge } from "@/components/lemma/ui/badge";
import { Button } from "@/components/lemma/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/lemma/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/lemma/ui/dialog";
import { Separator } from "@/components/lemma/ui/separator";
import { Textarea } from "@/components/lemma/ui/textarea";

export type LemmaActionSurfaceAppearance = "default" | "minimal" | "borderless" | "contained";
export type LemmaActionSurfaceDensity = "compact" | "comfortable" | "spacious";
export type LemmaActionSurfaceRadius = "none" | "sm" | "md" | "lg" | "xl";
export type LemmaActionSurfaceVariant = "button" | "line" | "panel";
export type LemmaActionProgressSurface = "modal" | "inline" | "none";

export interface LemmaActionContext {
  [key: string]: unknown;
}

type ActionInputValue =
  | Record<string, unknown>
  | ((context: LemmaActionContext) => Record<string, unknown>);

interface BaseLemmaActionDefinition {
  label: ReactNode;
  description?: ReactNode;
  input?: ActionInputValue;
  runningLabels?: string[];
  waitingLabel?: ReactNode;
  successLabel?: ReactNode;
  failureLabel?: ReactNode;
}

export interface LemmaDirectActionDefinition extends BaseLemmaActionDefinition {
  kind: "direct";
  run: (input: Record<string, unknown>, context: LemmaActionContext) => Promise<unknown> | unknown;
}

export interface LemmaFunctionActionDefinition extends BaseLemmaActionDefinition {
  kind: "function";
  functionName: string;
}

export interface LemmaWorkflowActionDefinition extends BaseLemmaActionDefinition {
  kind: "workflow";
  workflowName: string;
}

export interface LemmaAgentActionDefinition extends BaseLemmaActionDefinition {
  kind: "agent";
  agentName: string;
  instructions?: string | null;
  messageMetadata?: Record<string, unknown> | null;
}

export type LemmaActionDefinition =
  | LemmaDirectActionDefinition
  | LemmaFunctionActionDefinition
  | LemmaWorkflowActionDefinition
  | LemmaAgentActionDefinition;

export interface LemmaActionExecution {
  kind: LemmaActionDefinition["kind"];
  id: string | null;
  status: string;
  error: string | null;
  startedAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
  input: Record<string, unknown> | null;
  output: unknown;
  isRunning: boolean;
  isWaiting: boolean;
  isFinished: boolean;
  messages: ConversationMessage[];
  steps: FlowRun["step_history"];
  conversation?: Conversation | null;
}

export interface LemmaActionSurfaceProps {
  client: LemmaClient;
  podId?: string;
  action: LemmaActionDefinition;
  context?: LemmaActionContext;
  title?: ReactNode;
  description?: ReactNode;
  variant?: LemmaActionSurfaceVariant;
  progressSurface?: LemmaActionProgressSurface;
  autoOpenProgress?: boolean;
  progressTitle?: ReactNode;
  progressDescription?: ReactNode;
  progressOpen?: boolean;
  defaultProgressOpen?: boolean;
  onProgressOpenChange?: (open: boolean) => void;
  executionId?: string | null;
  defaultExecutionId?: string | null;
  onExecutionIdChange?: (id: string | null) => void;
  onExecutionChange?: (execution: LemmaActionExecution | null) => void;
  enabled?: boolean;
  disabled?: boolean;
  appearance?: LemmaActionSurfaceAppearance;
  density?: LemmaActionSurfaceDensity;
  radius?: LemmaActionSurfaceRadius;
  className?: string;
  headerActions?: ReactNode;
  onSuccess?: (execution: LemmaActionExecution) => void;
  onError?: (error: unknown, execution: LemmaActionExecution | null) => void;
}

interface DirectExecutionState {
  status: string;
  error: string | null;
  output: unknown;
  input: Record<string, unknown> | null;
  startedAt?: string;
  updatedAt?: string;
  completedAt?: string;
}

interface AgentToolMessageMetadata {
  toolName?: string;
  toolCallId?: string;
  messageType?: "tool_call" | "tool_return";
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
}

type AgentTimelineEntry =
  | {
      type: "message";
      id: string;
      role: string;
      createdAt: string;
      text: string;
    }
  | {
      type: "tool";
      id: string;
      toolName: string;
      toolCallId?: string;
      createdAt: string;
      args?: Record<string, unknown>;
      result?: Record<string, unknown>;
      isExecuting: boolean;
      isSuccess?: boolean;
      error?: string;
    };

function surfaceRadiusClassName(radius: LemmaActionSurfaceRadius): string {
  if (radius === "none") return "rounded-none";
  if (radius === "sm") return "rounded-sm";
  if (radius === "md") return "rounded-md";
  if (radius === "xl") return "rounded-xl";
  return "rounded-lg";
}

function normalizeStatus(status: unknown): string {
  return typeof status === "string" && status.trim().length > 0 ? status.trim().toUpperCase() : "IDLE";
}

function isFinishedStatus(status: string): boolean {
  return ["COMPLETED", "FAILED", "CANCELLED", "STOPPED"].includes(status);
}

function statusLabel(status: string): string {
  if (status === "IDLE") return "Idle";
  return status.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function statusBadgeClassName(status: string): string {
  if (status === "COMPLETED") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700";
  if (status === "FAILED") return "border-destructive/30 bg-destructive/10 text-destructive";
  if (status === "WAITING") return "border-amber-500/30 bg-amber-500/10 text-amber-700";
  if (status === "RUNNING" || status === "EXECUTING" || status === "PENDING") return "border-primary/30 bg-primary/10 text-primary";
  if (status === "CANCELLED" || status === "STOPPED") return "border-border/50 bg-muted text-muted-foreground";
  return "border-border/50 bg-muted text-muted-foreground";
}

function statusIcon(status: string) {
  if (status === "COMPLETED") return <CheckCircle2 className="size-4" />;
  if (status === "FAILED") return <XCircle className="size-4" />;
  if (status === "WAITING") return <AlertCircle className="size-4" />;
  if (status === "RUNNING" || status === "EXECUTING" || status === "PENDING") return <Loader2 className="size-4 animate-spin" />;
  return <Sparkles className="size-4" />;
}

function formatDateTime(value?: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsed);
}

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function stringifyAgentInput(input: Record<string, unknown>): string {
  if (Object.keys(input).length === 0) return "";
  const prompt = input.prompt ?? input.message ?? input.content;
  if (typeof prompt === "string" && prompt.trim().length > 0 && Object.keys(input).length === 1) {
    return prompt.trim();
  }
  return stringifyValue(input);
}

function plainLabel(value: ReactNode): string {
  if (value == null || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map((entry) => plainLabel(entry)).join(" ").trim();
  return "";
}

function resolveActionInput(input: ActionInputValue | undefined, context: LemmaActionContext): Record<string, unknown> {
  if (!input) return { ...context };
  const resolved = typeof input === "function" ? input(context) : input;
  return {
    ...context,
    ...(resolved ?? {}),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function parseJsonRecord(value: unknown): Record<string, unknown> | undefined {
  if (isRecord(value)) return value;
  if (typeof value !== "string") return undefined;

  try {
    const parsed = JSON.parse(value);
    return isRecord(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function getConversationMessageContentRecord(message: ConversationMessage): Record<string, unknown> | null {
  return isRecord(message.content) ? message.content : null;
}

function getConversationMessageMetadata(message: ConversationMessage): AgentToolMessageMetadata | undefined {
  const metadata = isRecord(message.metadata) ? message.metadata : null;
  const content = getConversationMessageContentRecord(message);

  const toolName = typeof metadata?.tool_name === "string"
    ? metadata.tool_name
    : typeof content?.tool_name === "string"
      ? content.tool_name
      : undefined;
  const toolCallId = typeof metadata?.tool_call_id === "string"
    ? metadata.tool_call_id
    : typeof content?.tool_call_id === "string"
      ? content.tool_call_id
      : undefined;
  const rawMessageType = metadata?.message_type;
  const contentType = content?.type;
  const messageType = rawMessageType === "tool_call" || rawMessageType === "tool_return"
    ? rawMessageType
    : contentType === "tool_call" || contentType === "tool_return"
      ? contentType
      : undefined;
  const args = parseJsonRecord(metadata?.args) || parseJsonRecord(content?.tool_input);
  const result = parseJsonRecord(metadata?.result) || parseJsonRecord(content?.tool_output);

  if (!toolName && !toolCallId && !messageType && !args && !result) {
    return undefined;
  }

  return {
    toolName,
    toolCallId,
    messageType,
    args,
    result,
  };
}

function extractConversationMessageText(message: ConversationMessage): string {
  const content = message.content as Record<string, unknown> | null;
  if (content && typeof content.content === "string" && content.content.trim().length > 0) {
    return content.content;
  }
  return stringifyValue(message.content);
}

function isAssistantLikeRole(role: string): boolean {
  const normalized = role.trim().toLowerCase();
  return normalized === "assistant" || normalized === "ai";
}

function isToolLikeRole(role: string): boolean {
  return role.trim().toLowerCase() === "tool";
}

function isToolCallMessage(message: ConversationMessage): boolean {
  const metadata = getConversationMessageMetadata(message);
  const content = getConversationMessageContentRecord(message);
  return metadata?.messageType === "tool_call"
    || (isAssistantLikeRole(message.role) && typeof content?.tool_name === "string");
}

function isToolReturnMessage(message: ConversationMessage): boolean {
  const metadata = getConversationMessageMetadata(message);
  const content = getConversationMessageContentRecord(message);
  return metadata?.messageType === "tool_return"
    || isToolLikeRole(message.role)
    || typeof content?.tool_output !== "undefined";
}

function humanizeKey(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatToolName(toolName: string): string {
  const cleaned = toolName.trim();
  return cleaned.length > 0 ? humanizeKey(cleaned) : "Tool";
}

function summarizeRecord(record: Record<string, unknown> | undefined, preferredKeys: string[]): string | null {
  if (!record) return null;
  for (const key of preferredKeys) {
    const value = record[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }

  const firstEntry = Object.entries(record).find(([, value]) => {
    if (typeof value === "string") return value.trim().length > 0;
    if (typeof value === "number" || typeof value === "boolean") return true;
    return false;
  });

  if (!firstEntry) return null;
  return `${humanizeKey(firstEntry[0])}: ${String(firstEntry[1])}`;
}

function summarizeToolActivity(entry: Extract<AgentTimelineEntry, { type: "tool" }>): string {
  if (entry.isExecuting) {
    return summarizeRecord(entry.args, ["query", "cmd", "path", "table", "resourceId", "resource_id"]) || "Running";
  }
  if (entry.isSuccess === false) {
    return entry.error || summarizeRecord(entry.result, ["error", "message", "stderr"]) || "Tool failed";
  }
  return summarizeRecord(entry.result, ["message", "stdout", "result", "resourceId", "resource_id"]) || "Completed";
}

function mergeAgentMessages(messages: ConversationMessage[]): AgentTimelineEntry[] {
  const entries: AgentTimelineEntry[] = [];
  const processedIds = new Set<string>();

  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (processedIds.has(message.id)) continue;

    if (isToolCallMessage(message)) {
      const metadata = getConversationMessageMetadata(message);
      const content = getConversationMessageContentRecord(message);
      const toolCallId = metadata?.toolCallId || (typeof content?.tool_call_id === "string" ? content.tool_call_id : undefined);
      const resultMessage = messages.slice(index + 1).find((candidate) => {
        if (processedIds.has(candidate.id) || !isToolReturnMessage(candidate)) return false;
        const candidateMetadata = getConversationMessageMetadata(candidate);
        const candidateContent = getConversationMessageContentRecord(candidate);
        const candidateToolCallId = candidateMetadata?.toolCallId
          || (typeof candidateContent?.tool_call_id === "string" ? candidateContent.tool_call_id : undefined);
        if (!toolCallId || !candidateToolCallId) return false;
        return candidateToolCallId === toolCallId;
      });
      const resultMetadata = resultMessage ? getConversationMessageMetadata(resultMessage) : undefined;
      const resultContent = resultMessage ? getConversationMessageContentRecord(resultMessage) : null;
      const result = resultMetadata?.result || parseJsonRecord(resultContent?.tool_output);
      const error = typeof result?.error === "string"
        ? result.error
        : typeof result?.message === "string" && result.success === false
          ? result.message
          : undefined;

      entries.push({
        type: "tool",
        id: message.id,
        toolName: metadata?.toolName || (typeof content?.tool_name === "string" ? content.tool_name : "tool"),
        toolCallId,
        createdAt: message.created_at,
        args: metadata?.args || parseJsonRecord(content?.tool_input),
        result,
        isExecuting: !resultMessage,
        isSuccess: typeof result?.success === "boolean" ? result.success : resultMessage ? true : undefined,
        error,
      });

      processedIds.add(message.id);
      if (resultMessage) {
        processedIds.add(resultMessage.id);
      }
      continue;
    }

    if (isToolReturnMessage(message)) {
      const metadata = getConversationMessageMetadata(message);
      const content = getConversationMessageContentRecord(message);
      const result = metadata?.result || parseJsonRecord(content?.tool_output);
      entries.push({
        type: "tool",
        id: message.id,
        toolName: metadata?.toolName || (typeof content?.tool_name === "string" ? content.tool_name : "tool"),
        toolCallId: metadata?.toolCallId || (typeof content?.tool_call_id === "string" ? content.tool_call_id : undefined),
        createdAt: message.created_at,
        result,
        isExecuting: false,
        isSuccess: typeof result?.success === "boolean" ? result.success : undefined,
        error: typeof result?.error === "string" ? result.error : undefined,
      });
      processedIds.add(message.id);
      continue;
    }

    entries.push({
      type: "message",
      id: message.id,
      role: message.role,
      createdAt: message.created_at,
      text: extractConversationMessageText(message),
    });
    processedIds.add(message.id);
  }

  return entries;
}

function stepLabel(step: NonNullable<FlowRun["step_history"]>[number]): string {
  if (!step || typeof step !== "object") return "Step";
  const candidate = "node_id" in step ? step.node_id : "";
  return typeof candidate === "string" && candidate.trim().length > 0 ? candidate : "Step";
}

function useControllableBoolean(
  controlled: boolean | undefined,
  defaultValue: boolean,
  onChange?: (value: boolean) => void,
) {
  const [uncontrolled, setUncontrolled] = useState(defaultValue);
  const value = controlled ?? uncontrolled;
  const setValue = useCallback((next: boolean) => {
    if (controlled === undefined) {
      setUncontrolled(next);
    }
    onChange?.(next);
  }, [controlled, onChange]);
  return [value, setValue] as const;
}

function useControllableString(
  controlled: string | null | undefined,
  defaultValue: string | null | undefined,
  onChange?: (value: string | null) => void,
) {
  const [uncontrolled, setUncontrolled] = useState<string | null>(defaultValue ?? null);
  const value = controlled ?? uncontrolled;
  const setValue = useCallback((next: string | null) => {
    if (controlled === undefined) {
      setUncontrolled(next);
    }
    onChange?.(next);
  }, [controlled, onChange]);
  return [value, setValue] as const;
}

export function LemmaActionSurface({
  client,
  podId,
  action,
  context = {},
  title,
  description,
  variant = "button",
  progressSurface = "modal",
  autoOpenProgress,
  progressTitle,
  progressDescription,
  progressOpen: controlledProgressOpen,
  defaultProgressOpen = false,
  onProgressOpenChange,
  executionId: controlledExecutionId,
  defaultExecutionId = null,
  onExecutionIdChange,
  onExecutionChange,
  enabled = true,
  disabled = false,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
  headerActions,
  onSuccess,
  onError,
}: LemmaActionSurfaceProps) {
  const [progressOpen, setProgressOpen] = useControllableBoolean(
    controlledProgressOpen,
    defaultProgressOpen,
    onProgressOpenChange,
  );
  const [executionId, setExecutionId] = useControllableString(
    controlledExecutionId,
    defaultExecutionId,
    onExecutionIdChange,
  );
  const [directExecution, setDirectExecution] = useState<DirectExecutionState | null>(null);
  const [busyLabelIndex, setBusyLabelIndex] = useState(0);
  const [workflowResumeInput, setWorkflowResumeInput] = useState("{}");
  const [agentReply, setAgentReply] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const workflowAction = action.kind === "workflow" ? action : null;
  const functionAction = action.kind === "function" ? action : null;
  const agentAction = action.kind === "agent" ? action : null;

  const workflowRun = useWorkflowStart({
    client,
    podId,
    workflowName: workflowAction?.workflowName ?? "",
    enabled: enabled && action.kind === "workflow",
    autoLoad: enabled && action.kind === "workflow",
    autoPoll: true,
  });
  const functionRun = useFunctionRun({
    client,
    podId,
    functionName: functionAction?.functionName,
    autoPoll: true,
  });
  const agentConversation = useConversationMessages({
    client,
    podId,
    agentName: agentAction?.agentName,
    conversationId: action.kind === "agent" ? executionId : null,
    autoLoad: enabled && action.kind === "agent",
    autoResume: true,
    syncOnTurnEnd: true,
  });

  useEffect(() => {
    if (action.kind === "workflow") {
      if (workflowRun.runId !== executionId) {
        workflowRun.setRunId(executionId);
      }
      if (functionRun.runId !== null) {
        functionRun.setRunId(null);
      }
      return;
    }
    if (action.kind === "function") {
      if (functionRun.runId !== executionId) {
        functionRun.setRunId(executionId);
      }
      if (workflowRun.runId !== null) {
        workflowRun.setRunId(null);
      }
      return;
    }
    if (action.kind === "agent") {
      if (workflowRun.runId !== null) {
        workflowRun.setRunId(null);
      }
      if (functionRun.runId !== null) {
        functionRun.setRunId(null);
      }
      return;
    }
    if (workflowRun.runId !== null) {
      workflowRun.setRunId(null);
    }
    if (functionRun.runId !== null) {
      functionRun.setRunId(null);
    }
  }, [
    action.kind,
    executionId,
    functionRun.runId,
    functionRun.setRunId,
    workflowRun.runId,
    workflowRun.setRunId,
  ]);

  const resolvedInput = useMemo(
    () => resolveActionInput(action.input, context),
    [action.input, context],
  );

  const execution = useMemo<LemmaActionExecution | null>(() => {
    if (action.kind === "workflow") {
      const status = normalizeStatus(workflowRun.status);
      const run = workflowRun.run;
      if (!run && !workflowRun.runId) return null;
      return {
        kind: "workflow",
        id: workflowRun.runId,
        status,
        error: run?.status === "FAILED" && run.execution_context && typeof run.execution_context.error === "string"
          ? String(run.execution_context.error)
          : workflowRun.error?.message ?? null,
        startedAt: run?.started_at ?? run?.created_at ?? null,
        updatedAt: run?.updated_at ?? null,
        completedAt: run?.completed_at ?? null,
        input: resolvedInput,
        output: run?.execution_context ?? null,
        isRunning: !isFinishedStatus(status) && status !== "WAITING" && status !== "IDLE",
        isWaiting: status === "WAITING",
        isFinished: isFinishedStatus(status),
        messages: [],
        steps: run?.step_history ?? [],
      };
    }
    if (action.kind === "function") {
      const status = normalizeStatus(functionRun.status);
      const run = functionRun.run;
      if (!run && !functionRun.runId) return null;
      return {
        kind: "function",
        id: functionRun.runId,
        status,
        error: run?.error ?? functionRun.error?.message ?? null,
        startedAt: run?.started_at ? String(run.started_at) : run?.created_at ? String(run.created_at) : null,
        updatedAt: run?.completed_at ? String(run.completed_at) : run?.started_at ? String(run.started_at) : null,
        completedAt: run?.completed_at ? String(run.completed_at) : null,
        input: (run?.input_data as Record<string, unknown> | null) ?? resolvedInput,
        output: run?.output_data ?? null,
        isRunning: !isFinishedStatus(status) && status !== "IDLE",
        isWaiting: false,
        isFinished: isFinishedStatus(status),
        messages: [],
        steps: [],
      };
    }
    if (action.kind === "agent") {
      const rawStatus = normalizeStatus(agentConversation.status);
      const conversation = agentConversation.conversation;
      const conversationId = agentConversation.conversationId;
      if (!conversation && !conversationId) return null;
      const isFinished = !agentConversation.isRunning && !!agentConversation.latestAssistantMessage;
      const status = isFinished && rawStatus === "WAITING" ? "COMPLETED" : rawStatus;
      return {
        kind: "agent",
        id: conversationId,
        status,
        error: agentConversation.error?.message ?? null,
        startedAt: conversation?.created_at ?? null,
        updatedAt: conversation?.updated_at ?? null,
        completedAt: isFinished ? conversation?.updated_at ?? null : null,
        input: resolvedInput,
        output: agentConversation.finalOutput ?? agentConversation.output ?? null,
        isRunning: agentConversation.isRunning,
        isWaiting: status === "WAITING" && !isFinished,
        isFinished,
        messages: agentConversation.messages,
        steps: [],
        conversation,
      };
    }
    if (!directExecution) return null;
    const status = normalizeStatus(directExecution.status);
    return {
      kind: "direct",
      id: executionId,
      status,
      error: directExecution.error,
      startedAt: directExecution.startedAt ?? null,
      updatedAt: directExecution.updatedAt ?? null,
      completedAt: directExecution.completedAt ?? null,
      input: directExecution.input,
      output: directExecution.output,
      isRunning: !isFinishedStatus(status) && status !== "IDLE",
      isWaiting: false,
      isFinished: isFinishedStatus(status),
      messages: [],
      steps: [],
    };
  }, [
    action.kind,
    agentConversation.conversation,
    agentConversation.conversationId,
    agentConversation.error?.message,
    agentConversation.finalOutput,
    agentConversation.isRunning,
    agentConversation.latestAssistantMessage,
    agentConversation.messages,
    agentConversation.output,
    agentConversation.status,
    directExecution,
    executionId,
    functionRun.error?.message,
    functionRun.run,
    functionRun.runId,
    functionRun.status,
    resolvedInput,
    workflowRun.error?.message,
    workflowRun.run,
    workflowRun.runId,
    workflowRun.status,
  ]);

  useEffect(() => {
    if (!execution) return;
    if (execution.id !== executionId) {
      setExecutionId(execution.id);
    }
  }, [execution, executionId, setExecutionId]);

  useEffect(() => {
    onExecutionChange?.(execution);
    if (!execution) return;
    if (execution.status === "COMPLETED") {
      onSuccess?.(execution);
    }
  }, [execution, onExecutionChange, onSuccess]);

  useEffect(() => {
    if (!execution || (!execution.isRunning && !execution.isWaiting)) {
      setBusyLabelIndex(0);
      return;
    }
    const timer = window.setInterval(() => {
      setBusyLabelIndex((current) => current + 1);
    }, 1400);
    return () => window.clearInterval(timer);
  }, [execution]);

  const busyLabels = action.runningLabels && action.runningLabels.length > 0
    ? action.runningLabels
    : action.kind === "agent"
      ? ["Starting…", "Thinking…", "Working…"]
      : action.kind === "workflow"
        ? ["Starting…", "Running…", "Advancing…"]
        : ["Running…", "Working…", "Finishing…"];

  const busyLabel = busyLabels[busyLabelIndex % busyLabels.length] ?? "Working…";
  const shouldAutoOpenProgress = autoOpenProgress ?? (action.kind === "workflow" || action.kind === "agent");
  const canOpenProgress = progressSurface === "modal" && execution !== null;

  const handleRun = useCallback(async () => {
    if (!enabled || disabled) return;
    setLocalError(null);
    try {
      if (action.kind === "workflow") {
        const run = await workflowRun.start(resolvedInput);
        if (run.id) setExecutionId(run.id);
      } else if (action.kind === "function") {
        const run = await functionRun.start(resolvedInput);
        if (run.id) setExecutionId(run.id);
      } else if (action.kind === "agent") {
        const content = stringifyAgentInput(resolvedInput);
        const conversation = await agentConversation.createConversation({
          agentName: action.agentName,
          title: content ? content.slice(0, 120) : action.agentName,
          instructions: action.instructions ?? undefined,
          setActive: true,
        });
        if (conversation.id) {
          setExecutionId(conversation.id);
        }
        if (content) {
          await agentConversation.sendMessage(content, {
            conversationId: conversation.id,
            metadata: action.messageMetadata ?? undefined,
            syncOnTurnEnd: true,
          });
        } else {
          await agentConversation.resume(conversation.id);
        }
      } else {
        const startedAt = new Date().toISOString();
        setDirectExecution({
          status: "RUNNING",
          error: null,
          output: null,
          input: resolvedInput,
          startedAt,
          updatedAt: startedAt,
        });
        if (shouldAutoOpenProgress && progressSurface === "modal") {
          setProgressOpen(true);
        }
        const result = await action.run(resolvedInput, context);
        const completedAt = new Date().toISOString();
        setDirectExecution({
          status: "COMPLETED",
          error: null,
          output: result,
          input: resolvedInput,
          startedAt,
          updatedAt: completedAt,
          completedAt,
        });
        return;
      }
      if (shouldAutoOpenProgress && progressSurface === "modal") {
        setProgressOpen(true);
      }
    } catch (error) {
      const nextMessage = error instanceof Error ? error.message : "Action failed.";
      setLocalError(nextMessage);
      if (action.kind === "direct") {
        const failedAt = new Date().toISOString();
        setDirectExecution({
          status: "FAILED",
          error: nextMessage,
          output: null,
          input: resolvedInput,
          startedAt: failedAt,
          updatedAt: failedAt,
          completedAt: failedAt,
        });
      }
      onError?.(error, execution);
      if (progressSurface === "modal") {
        setProgressOpen(true);
      }
    }
  }, [
    action,
    agentConversation,
    context,
    disabled,
    enabled,
    execution,
    functionRun,
    onError,
    progressSurface,
    resolvedInput,
    setExecutionId,
    setProgressOpen,
    shouldAutoOpenProgress,
    workflowRun,
  ]);

  const handleWorkflowResume = useCallback(async () => {
    if (action.kind !== "workflow" || !workflowRun.runId) return;
    setLocalError(null);
    try {
      const payload = workflowResumeInput.trim().length > 0
        ? JSON.parse(workflowResumeInput) as Record<string, unknown>
        : {};
      const resumed = await workflowRun.resume({ runId: workflowRun.runId, inputs: payload });
      if (resumed.id) setExecutionId(resumed.id);
    } catch (error) {
      const nextMessage = error instanceof Error ? error.message : "Failed to resume workflow.";
      setLocalError(nextMessage);
      onError?.(error, execution);
    }
  }, [action.kind, execution, onError, setExecutionId, workflowResumeInput, workflowRun]);

  const handleAgentReply = useCallback(async () => {
    if (action.kind !== "agent" || agentReply.trim().length === 0) return;
    setLocalError(null);
    try {
      await agentConversation.sendMessage(agentReply.trim(), {
        conversationId: execution?.id,
        metadata: action.messageMetadata ?? undefined,
        syncOnTurnEnd: true,
      });
      setAgentReply("");
    } catch (error) {
      const nextMessage = error instanceof Error ? error.message : "Failed to submit input.";
      setLocalError(nextMessage);
      onError?.(error, execution);
    }
  }, [action, agentConversation, agentReply, execution, onError]);

  const currentStatus = execution?.status ?? "IDLE";
  const statusText = execution?.isRunning
    ? busyLabel
    : execution?.isWaiting
      ? plainLabel(action.waitingLabel ?? "Needs input") || "Needs input"
      : currentStatus === "COMPLETED"
        ? plainLabel(action.successLabel ?? "Completed") || "Completed"
        : currentStatus === "FAILED"
          ? plainLabel(action.failureLabel ?? "Retry") || "Retry"
          : plainLabel(action.label) || "Run";

  const isBusy = execution?.isRunning || execution?.isWaiting || (action.kind === "workflow" && workflowRun.isStarting);
  const triggerDisabled = !enabled || disabled || (action.kind === "direct" && directExecution?.status === "RUNNING");
  const showOpenProgress = canOpenProgress && execution !== null && currentStatus !== "IDLE";
  const surfaceDensityClassName =
    density === "compact" ? "gap-2 p-3" : density === "spacious" ? "gap-4 p-5" : "gap-3 p-4";
  const surfaceClassName = cn(
    "flex flex-col border",
    appearance === "minimal" || appearance === "borderless"
      ? "border-border/30 bg-transparent shadow-none"
      : appearance === "contained"
        ? "border-border/60 bg-card shadow-sm"
        : "border-border/50 bg-background shadow-sm",
    surfaceRadiusClassName(radius),
    surfaceDensityClassName,
  );

  const summaryTitle = title ?? action.label;
  const summaryDescription = description ?? action.description;
  const executionError = execution?.error ?? localError;
  const executionOutput = execution?.output;
  const agentTimeline = useMemo(
    () => action.kind === "agent" ? mergeAgentMessages(execution?.messages ?? []) : [],
    [action.kind, execution?.messages],
  );

  const summary = (
    <>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={cn(
            "flex size-8 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground",
            surfaceRadiusClassName(radius),
          )}>
            {action.kind === "workflow" ? <Workflow className="size-4" /> : action.kind === "agent" ? <Bot className="size-4" /> : <Play className="size-4" />}
          </span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-foreground">{summaryTitle}</div>
            {summaryDescription ? (
              <div className="mt-0.5 text-xs text-muted-foreground">{summaryDescription}</div>
            ) : null}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {execution ? (
          <Badge variant="outline" className={cn("gap-1.5", statusBadgeClassName(currentStatus))}>
            {statusIcon(currentStatus)}
            {statusLabel(currentStatus)}
          </Badge>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant={variant === "button" ? "default" : "outline"}
          disabled={triggerDisabled && !showOpenProgress}
          onClick={() => {
            if (showOpenProgress && (execution?.isRunning || execution?.isWaiting)) {
              setProgressOpen(true);
              return;
            }
            void handleRun();
          }}
          className={cn(
            variant === "button" ? "min-w-[9rem]" : "h-8 px-3 text-xs",
            isBusy && "relative",
          )}
        >
          {isBusy ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : <Play className="mr-2 size-3.5" />}
          {statusText}
        </Button>
        {showOpenProgress ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 px-2 text-xs"
            onClick={() => setProgressOpen(true)}
          >
            View progress
            <ChevronRight className="ml-1 size-3.5" />
          </Button>
        ) : null}
        {headerActions}
      </div>
    </>
  );

  return (
    <>
      {variant === "button" ? (
        <div className={cn("inline-flex", className)}>
          <Button
            type="button"
            disabled={triggerDisabled && !showOpenProgress}
            onClick={() => {
              if (showOpenProgress && (execution?.isRunning || execution?.isWaiting)) {
                setProgressOpen(true);
                return;
              }
              void handleRun();
            }}
            className={cn("min-w-[11rem]", isBusy && "relative")}
          >
            {isBusy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Play className="mr-2 size-4" />}
            {statusText}
          </Button>
        </div>
      ) : variant === "line" ? (
        <div className={cn("flex flex-wrap items-center gap-3 border border-border/50 bg-background px-4 py-3 shadow-sm", surfaceRadiusClassName(radius), className)}>
          {summary}
        </div>
      ) : (
        <Card className={cn(surfaceClassName, className)}>
          <CardHeader className="gap-3 p-0">
            <div className="flex flex-wrap items-center justify-between gap-3">
              {summary}
            </div>
          </CardHeader>
          {execution ? (
            <>
              <Separator />
              <CardContent className="grid gap-3 p-0 text-sm">
                <div className="grid gap-1 text-muted-foreground">
                  {execution.id ? <div>Execution ID: <span className="text-foreground">{execution.id}</span></div> : null}
                  {formatDateTime(execution.startedAt) ? <div>Started: <span className="text-foreground">{formatDateTime(execution.startedAt)}</span></div> : null}
                  {formatDateTime(execution.updatedAt) ? <div>Updated: <span className="text-foreground">{formatDateTime(execution.updatedAt)}</span></div> : null}
                </div>
                {executionError ? (
                  <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {executionError}
                  </div>
                ) : null}
              </CardContent>
            </>
          ) : null}
        </Card>
      )}

      {progressSurface === "modal" ? (
        <Dialog open={progressOpen} onOpenChange={setProgressOpen}>
          <DialogContent className="max-h-[90vh] overflow-hidden sm:max-w-4xl">
            <DialogHeader>
              <DialogTitle>{progressTitle ?? summaryTitle}</DialogTitle>
              <DialogDescription>{progressDescription ?? summaryDescription ?? "Inspect live execution details and follow long-running work."}</DialogDescription>
            </DialogHeader>

            <div className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className={cn("gap-1.5", statusBadgeClassName(currentStatus))}>
                  {statusIcon(currentStatus)}
                  {statusLabel(currentStatus)}
                </Badge>
                {execution?.id ? <Badge variant="secondary">ID: {execution.id}</Badge> : null}
                {formatDateTime(execution?.startedAt) ? <Badge variant="secondary">Started {formatDateTime(execution?.startedAt)}</Badge> : null}
                {formatDateTime(execution?.updatedAt) ? <Badge variant="secondary">Updated {formatDateTime(execution?.updatedAt)}</Badge> : null}
              </div>

              {execution?.input && Object.keys(execution.input).length > 0 ? (
                <div className="grid gap-2">
                  <div className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Input</div>
                  <pre className="overflow-x-auto rounded-md border border-border/50 bg-muted/20 p-3 text-xs text-foreground">{stringifyValue(execution.input)}</pre>
                </div>
              ) : null}

              {action.kind === "workflow" ? (
                <div className="grid gap-3">
                  <div className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Steps</div>
                  {(execution?.steps?.length ?? 0) > 0 ? (
                    <div className="grid gap-2">
                      {execution?.steps?.map((step, index) => {
                        const stepStatus = normalizeStatus(step.status);
                        return (
                          <div key={`${stepLabel(step)}-${index}`} className="flex items-start gap-3 rounded-md border border-border/40 bg-muted/10 px-3 py-2">
                            <div className={cn("mt-0.5", stepStatus === "COMPLETED" ? "text-emerald-600" : stepStatus === "FAILED" ? "text-destructive" : stepStatus === "WAITING" ? "text-amber-600" : "text-primary")}>
                              {statusIcon(stepStatus)}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="text-sm font-medium text-foreground">{stepLabel(step)}</div>
                              <div className="mt-0.5 text-xs text-muted-foreground">{statusLabel(stepStatus)}</div>
                              {"error" in step && typeof step.error === "string" && step.error.trim().length > 0 ? (
                                <div className="mt-1 text-xs text-destructive">{step.error}</div>
                              ) : null}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="rounded-md border border-dashed border-border/50 px-4 py-6 text-sm text-muted-foreground">
                      Step history will appear as the workflow advances.
                    </div>
                  )}
                </div>
              ) : null}

              {action.kind === "agent" ? (
                <div className="grid gap-3">
                  <div className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Conversation</div>
                  {agentTimeline.length > 0 ? (
                    <div className="grid gap-2">
                      {agentTimeline.map((entry) => (
                        entry.type === "message" ? (
                          <div
                            key={entry.id}
                            className={cn(
                              "rounded-md border px-3 py-2 text-sm",
                              isAssistantLikeRole(entry.role)
                                ? "border-border/50 bg-muted/10"
                                : "border-primary/20 bg-primary/10",
                            )}
                          >
                            <div className="mb-1 flex items-center justify-between gap-3">
                              <div className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
                                {entry.role}
                              </div>
                              {formatDateTime(entry.createdAt) ? (
                                <div className="text-[11px] text-muted-foreground">{formatDateTime(entry.createdAt)}</div>
                              ) : null}
                            </div>
                            <div className="whitespace-pre-wrap text-foreground">{entry.text}</div>
                          </div>
                        ) : (
                          <div key={entry.id} className="rounded-lg border border-border/60 bg-card/40 px-3 py-3 shadow-none">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span
                                    className={cn(
                                      "size-2 rounded-full",
                                      entry.isExecuting
                                        ? "bg-primary animate-pulse"
                                        : entry.isSuccess === false
                                          ? "bg-destructive"
                                          : "bg-emerald-500",
                                    )}
                                  />
                                  <div className="truncate text-sm font-medium text-foreground">{formatToolName(entry.toolName)}</div>
                                  <Badge
                                    variant="outline"
                                    className={cn(
                                      "h-6 px-2 text-[11px]",
                                      entry.isExecuting
                                        ? statusBadgeClassName("RUNNING")
                                        : entry.isSuccess === false
                                          ? statusBadgeClassName("FAILED")
                                          : statusBadgeClassName("COMPLETED"),
                                    )}
                                  >
                                    {entry.isExecuting ? "Running" : entry.isSuccess === false ? "Failed" : "Done"}
                                  </Badge>
                                </div>
                                <div className="mt-1 text-xs text-muted-foreground">{summarizeToolActivity(entry)}</div>
                              </div>
                              <div className="flex flex-col items-end gap-1 text-[11px] text-muted-foreground">
                                {entry.toolCallId ? <span className="font-mono">{entry.toolCallId}</span> : null}
                                {formatDateTime(entry.createdAt) ? <span>{formatDateTime(entry.createdAt)}</span> : null}
                              </div>
                            </div>
                            {entry.args || entry.result ? (
                              <div className="mt-3 grid gap-2 md:grid-cols-2">
                                {entry.args ? (
                                  <details className="rounded-md border border-border/50 bg-background/70 p-2.5">
                                    <summary className="cursor-pointer list-none text-xs font-medium text-muted-foreground hover:text-foreground">
                                      Input JSON
                                    </summary>
                                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-xs text-foreground/80">
                                      {JSON.stringify(entry.args, null, 2)}
                                    </pre>
                                  </details>
                                ) : null}
                                {entry.result ? (
                                  <details className="rounded-md border border-border/50 bg-background/70 p-2.5">
                                    <summary className="cursor-pointer list-none text-xs font-medium text-muted-foreground hover:text-foreground">
                                      Output JSON
                                    </summary>
                                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-xs text-foreground/80">
                                      {JSON.stringify(entry.result, null, 2)}
                                    </pre>
                                  </details>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        )
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-md border border-dashed border-border/50 px-4 py-6 text-sm text-muted-foreground">
                      Live agent messages will appear here.
                    </div>
                  )}
                </div>
              ) : null}

              {executionOutput != null ? (
                <div className="grid gap-2">
                  <div className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Output</div>
                  <pre className="overflow-x-auto rounded-md border border-border/50 bg-muted/20 p-3 text-xs text-foreground">{stringifyValue(executionOutput)}</pre>
                </div>
              ) : null}

              {executionError ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {executionError}
                </div>
              ) : null}

              {action.kind === "workflow" && execution?.isWaiting ? (
                <div className="grid gap-2">
                  <div className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Resume workflow</div>
                  <Textarea
                    value={workflowResumeInput}
                    onChange={(event) => setWorkflowResumeInput(event.target.value)}
                    rows={6}
                    placeholder='{"approved": true}'
                  />
                  <div className="flex justify-end">
                    <Button type="button" size="sm" onClick={() => { void handleWorkflowResume(); }}>
                      Resume
                    </Button>
                  </div>
                </div>
              ) : null}

              {action.kind === "agent" && execution?.isWaiting ? (
                <div className="grid gap-2">
                  <div className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Reply</div>
                  <Textarea
                    value={agentReply}
                    onChange={(event) => setAgentReply(event.target.value)}
                    rows={4}
                    placeholder="Add the missing input for the agent"
                  />
                  <div className="flex justify-end">
                    <Button type="button" size="sm" onClick={() => { void handleAgentReply(); }} disabled={agentReply.trim().length === 0}>
                      Send input
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setProgressOpen(false)}>
                Close
              </Button>
              {currentStatus === "FAILED" || currentStatus === "CANCELLED" || currentStatus === "STOPPED" ? (
                <Button type="button" onClick={() => { void handleRun(); }}>
                  Retry
                </Button>
              ) : null}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}

      {progressSurface === "inline" && execution ? (
        <div className={cn("mt-3 rounded-md border border-border/50 bg-muted/10 p-3 text-sm", className && variant === "button" && "max-w-xl")}>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className={cn("gap-1.5", statusBadgeClassName(currentStatus))}>
              {statusIcon(currentStatus)}
              {statusLabel(currentStatus)}
            </Badge>
            {execution.id ? <span className="text-xs text-muted-foreground">ID: {execution.id}</span> : null}
          </div>
          {executionError ? <div className="mt-2 text-xs text-destructive">{executionError}</div> : null}
        </div>
      ) : null}
    </>
  );
}
