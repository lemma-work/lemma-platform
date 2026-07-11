// Agent message-display pipeline.
//
// Turning a raw stream of agent messages into the rows a chat UI renders is
// genuinely intricate: deduping tool invocations, merging tool results back into
// their calls, folding a turn's intermediate "thinking" into collapsible trace
// notes, clustering tool-only messages, and segmenting each turn into "worked
// for X" runs. lemma-frontend grew this engine; it's pure (no React/JSX/DOM), so
// it lives in the core now and the product consumes it — one normalization for
// the hooks, web components, and the app. Formatting/labels and JSX stay in the
// caller.
import { isFinalResultToolName } from "./output.js";
import { normalizeAgentToolName } from "./tool-names.js";
import type {
  AssistantMessagePart,
  AssistantRenderableMessage,
  AssistantToolInvocation,
} from "./renderable.js";

export interface DisplayMessageRow {
  id: string;
  message: AssistantRenderableMessage;
  sourceIndexes: number[];
}

export interface CompletedRunTraceGroupState {
  startIndex: number;
  endIndex: number;
  label: string;
}

export type PlanStatus = "pending" | "in_progress" | "completed";

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

// --- small utils ------------------------------------------------------------

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
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

function hasObjectEntries(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0;
}

function objectPayload(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

/** Compact duration label ("12s", "3m 4s"). */
export function formatDurationCompact(durationMs: number): string {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes <= 0) return `${totalSeconds}s`;
  if (seconds <= 0) return `${minutes}m`;
  return `${minutes}m ${seconds}s`;
}

function messageTimeMs(message?: AssistantRenderableMessage | null): number | null {
  if (!(message?.createdAt instanceof Date) || Number.isNaN(message.createdAt.getTime())) return null;
  return message.createdAt.getTime();
}

// --- tool invocations -------------------------------------------------------

function toolInvocationKey(tool: AssistantToolInvocation): string {
  return tool.toolCallId || `${tool.toolName}:${JSON.stringify(tool.args ?? {})}`;
}

function preferToolInvocation(current: AssistantToolInvocation, next: AssistantToolInvocation): AssistantToolInvocation {
  if (next.state === "result") {
    return {
      ...current,
      ...next,
      args: next.args ?? current.args,
      result: next.result ?? current.result,
      state: "result",
    };
  }
  if (current.state === "result") return current;
  return {
    ...current,
    ...next,
    args: current.args ?? next.args,
    result: current.result ?? next.result,
  };
}

/** All tool invocations on a message (from `.parts` and `.toolInvocations`),
 * deduped by call id (or tool+args), preferring completed results. */
export function dedupToolInvocations(message: AssistantRenderableMessage): AssistantToolInvocation[] {
  const invocations: AssistantToolInvocation[] = [];
  const indexes = new Map<string, number>();

  const addInvocation = (invocation: AssistantToolInvocation) => {
    const key = toolInvocationKey(invocation);
    const existingIndex = indexes.get(key);
    if (typeof existingIndex === "number") {
      invocations[existingIndex] = preferToolInvocation(invocations[existingIndex], invocation);
      return;
    }
    indexes.set(key, invocations.length);
    invocations.push(invocation);
  };

  (message.parts || []).forEach((part) => {
    if (part.type !== "tool") return;
    addInvocation(part.toolInvocation);
  });
  (message.toolInvocations || []).forEach((invocation) => {
    addInvocation(invocation);
  });

  return invocations;
}

function isLongRunningToolResult(invocation: AssistantToolInvocation): boolean {
  const result = invocation.result || {};
  return (
    result.completed === false
    || (typeof result.session_id === "string" && result.session_id.length > 0 && result.exit_code == null)
  );
}

/** Whether a tool invocation is still executing (not a result, or a long-running
 * session result). */
export function isToolInvocationActive(invocation: AssistantToolInvocation): boolean {
  return invocation.state !== "result" || isLongRunningToolResult(invocation);
}

// --- message accessors ------------------------------------------------------

function messageRecord(message: AssistantRenderableMessage): Record<string, unknown> {
  return message as unknown as Record<string, unknown>;
}

function contentRecord(message: AssistantRenderableMessage): Record<string, unknown> | null {
  const content = messageRecord(message).content;
  return content && typeof content === "object" && !Array.isArray(content)
    ? (content as Record<string, unknown>)
    : null;
}

function metadataRecord(message: AssistantRenderableMessage): Record<string, unknown> | null {
  const record = messageRecord(message);
  const metadata = record.metadata ?? record.message_metadata;
  return metadata && typeof metadata === "object" && !Array.isArray(metadata)
    ? (metadata as Record<string, unknown>)
    : null;
}

function messageAgentRunId(message: AssistantRenderableMessage): string | null {
  const record = messageRecord(message);
  const metadata = metadataRecord(message);
  const agentRunId = record.agent_run_id ?? record.agentRunId ?? metadata?.agent_run_id ?? metadata?.agentRunId;
  return typeof agentRunId === "string" && agentRunId.trim() ? agentRunId : null;
}

function messageFlag(message: AssistantRenderableMessage, snakeKey: string, camelKey: string): boolean {
  const content = contentRecord(message);
  const metadata = metadataRecord(message);
  return content?.[snakeKey] === true
    || content?.[camelKey] === true
    || metadata?.[snakeKey] === true
    || metadata?.[camelKey] === true;
}

function isFinalAnswerMessage(message: AssistantRenderableMessage): boolean {
  return messageFlag(message, "is_final_answer", "isFinalAnswer");
}

function isIntermediateAssistantMessage(message: AssistantRenderableMessage): boolean {
  return messageFlag(message, "is_intermediate_assistant_message", "isIntermediateAssistantMessage");
}

function normalizeAssistantDisplayText(text: string): string {
  return text.replace(/\r\n/g, "\n").trim().replace(/\n{3,}/g, "\n\n");
}

/** Plain text body of a message (string content, nested content, or text parts). */
export function messageTextContent(message: AssistantRenderableMessage): string {
  if (typeof message.content === "string") return normalizeAssistantDisplayText(message.content);

  const content = contentRecord(message);
  const contentText = content?.content ?? content?.text;
  if (typeof contentText === "string") return normalizeAssistantDisplayText(contentText);

  const textParts = (message.parts || [])
    .filter((part): part is Extract<AssistantMessagePart, { type: "text" }> => part.type === "text")
    .map((part) => normalizeAssistantDisplayText(part.text))
    .filter(Boolean);

  return normalizeAssistantDisplayText(textParts.join("\n\n"));
}

function hasMeaningfulTextPart(message: AssistantRenderableMessage): boolean {
  return (message.parts || []).some((part) => part.type === "text" && part.text.trim().length > 0);
}

// --- tool-message (flat) accessors ------------------------------------------

function toolMessageKind(message: AssistantRenderableMessage): "tool_call" | "tool_return" | null {
  const metadata = metadataRecord(message);
  const type = message.kind ?? metadata?.message_type;
  if (type === "tool_call" || type === "tool_return") return type;
  if (String(message.role) === "tool") return "tool_return";
  return null;
}

function toolMessageName(message: AssistantRenderableMessage): string | null {
  const metadata = metadataRecord(message);
  const toolName = message.tool_name ?? metadata?.tool_name;
  return typeof toolName === "string" && toolName.trim() ? toolName : null;
}

function toolMessageCallId(message: AssistantRenderableMessage): string | null {
  const metadata = metadataRecord(message);
  const toolCallId = message.tool_call_id ?? metadata?.tool_call_id;
  return typeof toolCallId === "string" && toolCallId.trim() ? toolCallId : null;
}

function toolMessageInput(message: AssistantRenderableMessage): Record<string, unknown> | undefined {
  const metadata = metadataRecord(message);
  return objectPayload(message.tool_args ?? metadata?.args);
}

function toolMessageOutput(message: AssistantRenderableMessage): Record<string, unknown> | undefined {
  const metadata = metadataRecord(message);
  return objectPayload(message.tool_result ?? metadata?.result);
}

function messageHasToolActivity(message: AssistantRenderableMessage): boolean {
  const rawToolName = toolMessageKind(message) === "tool_call" ? toolMessageName(message) : null;
  return (!!rawToolName && !isFinalResultToolName(rawToolName))
    || dedupToolInvocations(message).some((invocation) => !isFinalResultToolName(invocation.toolName))
    || (message.parts || []).some((part) => (
      part.type === "tool" && !isFinalResultToolName(part.toolInvocation.toolName)
    ));
}

// --- run / trace classification ---------------------------------------------

function finalAnswerRunIds(messages: AssistantRenderableMessage[]): Set<string> {
  const runIds = new Set<string>();
  messages.forEach((message) => {
    if (!isFinalAnswerMessage(message)) return;
    const runId = messageAgentRunId(message);
    if (runId) runIds.add(runId);
  });
  return runIds;
}

function shouldConvertMessageToTraceNote(message: AssistantRenderableMessage): boolean {
  if (message.role !== "assistant") return false;
  if (isFinalAnswerMessage(message)) return false;
  if (toolMessageKind(message)) return false;
  if (messageHasToolActivity(message)) return false;
  const hasThinkingContent = message.kind === "THINKING";
  const hasReasoningPart = (message.parts || []).some((part) => part.type === "reasoning");
  if (!hasThinkingContent && !hasReasoningPart) return false;
  return messageTextContent(message).length > 0;
}

function shouldFoldIntermediateMessage(
  message: AssistantRenderableMessage,
  finalRunIds: Set<string>,
): boolean {
  if (message.role !== "assistant") return false;
  if (isFinalAnswerMessage(message)) return false;
  if (!isIntermediateAssistantMessage(message)) return false;
  if (!shouldConvertMessageToTraceNote(message)) return false;
  const runId = messageAgentRunId(message);
  return !!runId && finalRunIds.has(runId);
}

function completedTurnTraceDurations(messages: AssistantRenderableMessage[]): Map<number, number | undefined> {
  const traceDurations = new Map<number, number | undefined>();

  for (let start = 0; start < messages.length; start += 1) {
    const end = (() => {
      for (let index = start + 1; index < messages.length; index += 1) {
        if (messages[index].role === "user") return index;
      }
      return messages.length;
    })();

    const turnIndexes = Array.from({ length: end - start }, (_, offset) => start + offset);
    const finalIndex = [...turnIndexes]
      .reverse()
      .find((index) => messages[index].role === "assistant" && isFinalAnswerMessage(messages[index]));

    if (typeof finalIndex === "number") {
      const finalMessageTimeMs = messageTimeMs(messages[finalIndex]);
      turnIndexes.forEach((index) => {
        const message = messages[index];
        if (index >= finalIndex) return;
        if (!shouldConvertMessageToTraceNote(message)) return;
        const messageTime = messageTimeMs(message);
        const durationMs = finalMessageTimeMs && messageTime ? finalMessageTimeMs - messageTime : undefined;
        traceDurations.set(index, durationMs && durationMs > 0 ? durationMs : undefined);
      });
    }

    start = end - 1;
  }

  return traceDurations;
}

// --- message transforms -----------------------------------------------------

function messageWithTraceNote(message: AssistantRenderableMessage, durationMs?: number): AssistantRenderableMessage {
  const text = messageTextContent(message);
  const existingParts = (message.parts || []).filter((part) => part.type !== "text");
  const startedAtMs = message.createdAt instanceof Date && Number.isFinite(message.createdAt.getTime())
    ? message.createdAt.getTime()
    : undefined;
  const notePart = text
    ? [{
      id: `${message.id}-trace-note`,
      type: "reasoning",
      text,
      state: "done",
      durationMs,
      startedAtMs,
      traceNote: true,
    } as AssistantMessagePart]
    : [];

  return {
    ...message,
    content: "",
    parts: [...notePart, ...existingParts],
  };
}

function messageWithMergedToolResult(
  message: AssistantRenderableMessage,
  toolCallId: string,
  result: Record<string, unknown> | undefined,
  resultInvocation?: AssistantToolInvocation,
): AssistantRenderableMessage {
  const updateInvocation = (invocation: AssistantToolInvocation): AssistantToolInvocation => {
    if (invocation.toolCallId !== toolCallId) return invocation;
    return {
      ...invocation,
      toolName: invocation.toolName === "tool" && resultInvocation?.toolName && resultInvocation.toolName !== "tool"
        ? resultInvocation.toolName
        : invocation.toolName,
      args: hasObjectEntries(invocation.args) ? invocation.args : (resultInvocation?.args ?? invocation.args),
      state: "result",
      result: result ?? resultInvocation?.result ?? invocation.result,
    };
  };

  return {
    ...message,
    parts: message.parts?.map((part) => (
      part.type === "tool"
        ? { ...part, toolInvocation: updateInvocation(part.toolInvocation) }
        : part
    )),
    toolInvocations: message.toolInvocations?.map(updateInvocation),
  };
}

function messageWithRawToolCall(message: AssistantRenderableMessage): AssistantRenderableMessage {
  if (toolMessageKind(message) !== "tool_call") return message;
  if ((message.parts || []).some((part) => part.type === "tool") || (message.toolInvocations || []).length > 0) return message;

  const toolName = toolMessageName(message);
  const toolCallId = toolMessageCallId(message);
  if (!toolName || !toolCallId) return message;

  const invocation = {
    toolCallId,
    toolName,
    args: toolMessageInput(message),
    state: "call",
  } as AssistantToolInvocation;

  return {
    ...message,
    role: "assistant",
    content: "",
    parts: [
      ...((message.parts || []) as AssistantMessagePart[]),
      {
        id: `${message.id}-tool-${toolCallId}`,
        type: "tool",
        toolInvocation: invocation,
      } as AssistantMessagePart,
    ],
    toolInvocations: [...(message.toolInvocations || []), invocation],
  };
}

// --- collapse / cluster -----------------------------------------------------

function isCollapsibleAssistantMessage(message: AssistantRenderableMessage): boolean {
  if (message.role !== "assistant") return false;
  const hasTools = (message.toolInvocations?.length || 0) > 0 || (message.parts || []).some((part) => part.type === "tool");
  const hasReasoning = (message.parts || []).some((part) => part.type === "reasoning" && part.text.trim().length > 0);
  if (!hasTools && !hasReasoning) return false;
  return !hasMeaningfulTextPart(message) && (!message.content || message.content.trim().length === 0);
}

function assistantMessageHasRenderableContent(message: AssistantRenderableMessage): boolean {
  if (message.role !== "assistant") return true;
  if (messageTextContent(message).length > 0) return true;
  if (messageHasToolActivity(message)) return true;
  return (message.parts || []).some((part) => (
    part.type === "reasoning" && normalizeAssistantDisplayText(part.text).length > 0
  ));
}

function prepareMessagesForDisplay(
  messages: AssistantRenderableMessage[],
): Array<{ message: AssistantRenderableMessage; sourceIndexes: number[] }> {
  const completedRunIds = finalAnswerRunIds(messages);
  const completedTraceDurations = completedTurnTraceDurations(messages);
  const entries = messages.map((message, index) => ({
    message: messageWithRawToolCall(
      completedTraceDurations.has(index) || shouldFoldIntermediateMessage(message, completedRunIds)
        ? messageWithTraceNote(message, completedTraceDurations.get(index))
        : message,
    ),
    sourceIndexes: [index],
  }));
  const skipped = new Set<number>();
  const consumedCallIndexes = new Set<number>();
  const unresolvedByCallId = new Map<string, number[]>();
  const unresolvedByToolName = new Map<string, number[]>();

  const pushUnresolved = (map: Map<string, number[]>, key: string | null | undefined, index: number) => {
    if (!key) return;
    const existing = map.get(key) || [];
    if (!existing.includes(index)) existing.push(index);
    map.set(key, existing);
  };

  const consumeUnresolved = (map: Map<string, number[]>, key: string | null | undefined, beforeIndex: number): number | null => {
    if (!key) return null;
    const indexes = map.get(key);
    if (!indexes || indexes.length === 0) return null;

    for (let i = indexes.length - 1; i >= 0; i -= 1) {
      const candidate = indexes[i];
      if (candidate >= beforeIndex || consumedCallIndexes.has(candidate)) continue;
      indexes.splice(i, 1);
      consumedCallIndexes.add(candidate);
      return candidate;
    }

    return null;
  };

  const mergeResultIntoPriorCall = (
    entry: { message: AssistantRenderableMessage; sourceIndexes: number[] },
    index: number,
    toolCallId: string | null,
    toolName: string | null,
    result: Record<string, unknown> | undefined,
    resultInvocation?: AssistantToolInvocation,
  ): boolean => {
    const callIndex = consumeUnresolved(unresolvedByCallId, toolCallId, index)
      ?? consumeUnresolved(unresolvedByToolName, toolName, index);
    if (typeof callIndex !== "number") return false;

    const callEntry = entries[callIndex];
    const resolvedToolCallId = toolCallId || resultInvocation?.toolCallId || dedupToolInvocations(callEntry.message)[0]?.toolCallId;
    if (!resolvedToolCallId) return false;

    callEntry.message = messageWithMergedToolResult(
      callEntry.message,
      resolvedToolCallId,
      result,
      resultInvocation,
    );
    callEntry.sourceIndexes = [...callEntry.sourceIndexes, ...entry.sourceIndexes];
    return true;
  };

  const previousUnskippedIndex = (beforeIndex: number): number | null => {
    for (let i = beforeIndex - 1; i >= 0; i -= 1) {
      if (!skipped.has(i)) return i;
    }
    return null;
  };

  entries.forEach((entry, index) => {
    const invocations = dedupToolInvocations(entry.message);
    let mergedIntoPriorCall = false;

    if (toolMessageKind(entry.message) === "tool_return") {
      mergedIntoPriorCall = mergeResultIntoPriorCall(
        entry,
        index,
        toolMessageCallId(entry.message),
        toolMessageName(entry.message),
        toolMessageOutput(entry.message),
      ) || mergedIntoPriorCall;
    }

    invocations
      .filter((invocation) => invocation.state === "result")
      .forEach((invocation) => {
        mergedIntoPriorCall = mergeResultIntoPriorCall(
          entry,
          index,
          invocation.toolCallId,
          invocation.toolName,
          invocation.result,
          invocation,
        ) || mergedIntoPriorCall;
      });

    if (!mergedIntoPriorCall && invocations.length > 0 && invocations.every((invocation) => invocation.state !== "result") && isCollapsibleAssistantMessage(entry.message)) {
      const previousIndex = previousUnskippedIndex(index);
      for (let i = invocations.length - 1; i >= 0; i -= 1) {
        const invocation = invocations[i];
        const unresolvedIndexes = unresolvedByCallId.get(invocation.toolCallId);
        if (typeof previousIndex !== "number" || !unresolvedIndexes?.includes(previousIndex)) continue;

        mergedIntoPriorCall = mergeResultIntoPriorCall(
          entry,
          index,
          invocation.toolCallId,
          invocation.toolName,
          {},
          {
            ...invocation,
            state: "result",
            result: {},
          },
        );
        if (mergedIntoPriorCall) break;
      }
    }

    if (mergedIntoPriorCall && (toolMessageKind(entry.message) === "tool_return" || isCollapsibleAssistantMessage(entry.message))) {
      skipped.add(index);
    }

    invocations
      .filter((invocation) => invocation.state !== "result")
      .forEach((invocation) => {
        pushUnresolved(unresolvedByCallId, invocation.toolCallId, index);
        pushUnresolved(unresolvedByToolName, invocation.toolName, index);
      });
  });

  return entries.filter((_, index) => !skipped.has(index));
}

/** Turn a raw renderable-message list into the rows a chat UI renders: tool
 * results merged into calls, intermediate thinking folded to trace notes, and
 * tool-only messages clustered. */
export function buildDisplayMessageRows(messages: AssistantRenderableMessage[]): DisplayMessageRow[] {
  const rows: DisplayMessageRow[] = [];
  const displayEntries = prepareMessagesForDisplay(messages);

  for (let i = 0; i < displayEntries.length; i += 1) {
    const { message, sourceIndexes } = displayEntries[i];
    if (!assistantMessageHasRenderableContent(message)) {
      continue;
    }

    if (!isCollapsibleAssistantMessage(message)) {
      rows.push({ id: message.id, message, sourceIndexes });
      continue;
    }

    const cluster: AssistantRenderableMessage[] = [message];
    const clusterSourceIndexes = [...sourceIndexes];
    let j = i + 1;
    while (
      j < displayEntries.length
      && assistantMessageHasRenderableContent(displayEntries[j].message)
      && isCollapsibleAssistantMessage(displayEntries[j].message)
    ) {
      cluster.push(displayEntries[j].message);
      clusterSourceIndexes.push(...displayEntries[j].sourceIndexes);
      j += 1;
    }

    if (cluster.length === 1) {
      rows.push({ id: message.id, message, sourceIndexes: clusterSourceIndexes });
      i = j - 1;
      continue;
    }

    const mergedParts: AssistantMessagePart[] = [];
    const mergedToolInvocations: NonNullable<AssistantRenderableMessage["toolInvocations"]> = [];
    cluster.forEach((entry) => {
      if (entry.parts?.length) mergedParts.push(...entry.parts);
      if (entry.toolInvocations?.length) mergedToolInvocations.push(...entry.toolInvocations);
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
      sourceIndexes: clusterSourceIndexes,
    });

    i = j - 1;
  }

  return rows;
}

/** Index of the latest user message (−1 if none). */
export function latestUserIndex(messages: AssistantRenderableMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === "user") return index;
  }
  return -1;
}

// --- completed-run trace grouping -------------------------------------------

function isRunClosingMessage(message: AssistantRenderableMessage): boolean {
  if (message.role !== "assistant") return false;
  if (isIntermediateAssistantMessage(message)) return false;
  return isFinalAnswerMessage(message) || messageTextContent(message).length > 0;
}

function completedRunLabel(startMs: number | null, endMs: number | null): string {
  if (startMs !== null && endMs !== null && endMs > startMs) {
    return `Worked for ${formatDurationCompact(endMs - startMs)}`;
  }
  return "Worked";
}

function rowSourceTimesMs(row: DisplayMessageRow, messages: AssistantRenderableMessage[]): number[] {
  return row.sourceIndexes
    .map((sourceIndex) => messageTimeMs(messages[sourceIndex]))
    .filter((value): value is number => value !== null);
}

/** Segment display rows into "worked for X" runs: the trace rows before each
 * assistant answer become one collapsible group. Returns the group metadata and
 * the set of grouped row indexes. */
/**
 * Group each completed agent run into ONE "Worked for …" rollup that folds the
 * whole trace — every tool call and intermediate narration — leaving only the
 * run's final answer rendered outside the fold.
 *
 * A run is the contiguous block of assistant rows between user messages (one
 * turn). The trick that fixes the old behaviour: we don't close a run at the
 * first text message (which split a single run into one fold per narration);
 * we treat the LAST run-closing row in the turn as the answer and fold
 * everything before it. The active/streaming run is never folded — its work
 * stays visible while it runs (pass `isRunActive`).
 */
export function collectCompletedRunTraceGroups(
  rows: DisplayMessageRow[],
  messages: AssistantRenderableMessage[],
  isRunActive = false,
): {
  groupsByStartIndex: Map<number, CompletedRunTraceGroupState>;
  groupedIndexes: Set<number>;
} {
  const groupsByStartIndex = new Map<number, CompletedRunTraceGroupState>();
  const groupedIndexes = new Set<number>();

  let index = 0;
  while (index < rows.length) {
    if (rows[index].message.role !== "assistant") {
      index += 1;
      continue;
    }

    // One run = the contiguous run of assistant rows (bounded by user turns).
    const blockStart = index;
    let blockEnd = index;
    while (blockEnd + 1 < rows.length && rows[blockEnd + 1].message.role === "assistant") {
      blockEnd += 1;
    }
    index = blockEnd + 1;

    // Never collapse the live run — its activity should stay visible while working.
    if (isRunActive && blockEnd === rows.length - 1) continue;

    // The final answer is the last run-closing (real answer / text) row; the
    // trace before it is what folds. No closing row → a pure-tool run folds whole.
    let answerIndex = -1;
    for (let i = blockEnd; i >= blockStart; i -= 1) {
      if (isRunClosingMessage(rows[i].message)) {
        answerIndex = i;
        break;
      }
    }
    const foldEnd = answerIndex === -1 ? blockEnd : answerIndex - 1;
    if (foldEnd < blockStart) continue; // answer is the whole run; nothing to fold

    // Only fold runs that actually did work (tool activity); time the rollup.
    let activityStartMs: number | null = null;
    let sawActivity = false;
    for (let i = blockStart; i <= foldEnd; i += 1) {
      if (!messageHasToolActivity(rows[i].message)) continue;
      sawActivity = true;
      if (activityStartMs === null) {
        const times = rowSourceTimesMs(rows[i], messages);
        if (times.length) activityStartMs = Math.min(...times);
      }
    }
    if (!sawActivity) continue;

    const endRow = answerIndex === -1 ? rows[foldEnd] : rows[answerIndex];
    const endTimes = rowSourceTimesMs(endRow, messages);
    const endMs = endTimes.length ? Math.max(...endTimes) : null;

    groupsByStartIndex.set(blockStart, {
      startIndex: blockStart,
      endIndex: foldEnd,
      label: completedRunLabel(activityStartMs, endMs),
    });
    for (let i = blockStart; i <= foldEnd; i += 1) {
      groupedIndexes.add(i);
    }
  }

  return { groupsByStartIndex, groupedIndexes };
}

function rowIsAfterIndex(row: DisplayMessageRow, index: number): boolean {
  return row.sourceIndexes.some((sourceIndex) => sourceIndex > index);
}

// --- user approval ----------------------------------------------------------

/** Whether a tool is the higher-order approval gate (`request_approval`, or the
 * legacy `user_approval`). */
export function isUserApprovalToolName(toolName: string): boolean {
  const normalized = normalizeAgentToolName(toolName).toLowerCase();
  return normalized === "request_approval" || normalized === "user_approval";
}

/** Whether a tool is the multiple-choice question gate (`ask_user`). */
export function isAskUserToolName(toolName: string): boolean {
  return normalizeAgentToolName(toolName).toLowerCase() === "ask_user";
}

/** Whether a tool pauses the run for the user (an approval OR a question). Both
 * end the run (conversation -> WAITING) and resume via the approvals endpoint. */
export function isUserInteractionToolName(toolName: string): boolean {
  return isUserApprovalToolName(toolName) || isAskUserToolName(toolName);
}

/** Whether an interaction invocation should use the specialized question /
 * approval UI. Failed daemon fallbacks have a terminal result but no user
 * decision or answers, so they deliberately render as ordinary tool activity. */
export function isRenderableUserInteractionInvocation(
  invocation: AssistantToolInvocation,
): boolean {
  if (!isUserInteractionToolName(invocation.toolName)) return false;
  if (invocation.state !== "result") return true;
  const result = (invocation.result || {}) as Record<string, unknown>;
  const output = asRecord(result.output);
  return result.interaction_fallback !== true && output.interaction_fallback !== true;
}

export function userApprovalResolvedDecision(resultData: Record<string, unknown>): string | undefined {
  return asString(resultData.decision) || asString(asRecord(resultData.output).decision);
}

/** The pending interaction invocation in the current run (after the latest user
 * message), if the agent is waiting on one (request_approval or ask_user). */
export function findPendingUserApprovalInvocation(
  rows: DisplayMessageRow[],
  latestUser: number,
): AssistantToolInvocation | null {
  for (let rowIndex = rows.length - 1; rowIndex >= 0; rowIndex -= 1) {
    const row = rows[rowIndex];
    if (!rowIsAfterIndex(row, latestUser)) continue;
    const invocations = dedupToolInvocations(row.message);
    for (let invocationIndex = invocations.length - 1; invocationIndex >= 0; invocationIndex -= 1) {
      const invocation = invocations[invocationIndex];
      if (!isUserInteractionToolName(invocation.toolName)) continue;
      const resultData = (invocation.result || {}) as Record<string, unknown>;
      if (invocation.state !== "result" && !userApprovalResolvedDecision(resultData)) return invocation;
    }
  }
  return null;
}

// --- plan summary -----------------------------------------------------------

function normalizePlanStatus(rawStatus: unknown): PlanStatus {
  const status = typeof rawStatus === "string" ? rawStatus.trim().toLowerCase() : "";
  if (status === "completed" || status === "complete" || status === "done") return "completed";
  if (status === "in_progress" || status === "in-progress" || status === "running" || status === "active") return "in_progress";
  return "pending";
}

/** Parse one plan/to-do entry. Accepts a structured task object
 * (`{ step | title | content | text | task, status | state | done }`) or a
 * markdown checklist line (`- [ ] todo`, `- [x] done`, `- [~] in-progress`). */
function parsePlanStep(entry: unknown, index: number): PlanStepState | null {
  if (typeof entry === "string") {
    const match = /^\s*[-*]?\s*\[([ xX~-])\]\s*(.*)$/.exec(entry);
    if (match) {
      const mark = match[1].toLowerCase();
      const status: PlanStatus = mark === "x" ? "completed" : (mark === "~" || mark === "-") ? "in_progress" : "pending";
      const step = match[2].trim();
      return step ? { step, status } : null;
    }
    const step = entry.replace(/^\s*[-*]\s*/, "").trim();
    return step ? { step, status: "pending" } : null;
  }

  const obj = asRecord(entry);
  const step = asString(obj.step)
    || asString(obj.title)
    || asString(obj.content)
    || asString(obj.text)
    || asString(obj.task)
    || asString(obj.label)
    || asString(obj.name)
    || `Step ${index + 1}`;
  if (!step) return null;
  let status = normalizePlanStatus(obj.status ?? obj.state);
  if (status === "pending" && (obj.done === true || obj.completed === true)) status = "completed";
  return { step, status };
}

function parsePlanSteps(value: unknown): PlanStepState[] {
  return asArray(value)
    .map(parsePlanStep)
    .filter((entry): entry is PlanStepState => entry !== null);
}

/** The latest plan/to-do list in the conversation, projected into a render-ready
 * summary. Recognizes both the `update_plan` tool (`{ plan: [...] }`) and the
 * `write_todos` tool (`{ todos: [...] }`, markdown or structured). The result
 * payload — which carries the full, authoritative list for incremental
 * `write_todos` upserts — is preferred over the call args. */
export function latestPlanSummary(messages: AssistantRenderableMessage[]): PlanSummaryState | null {
  const displayMessages = prepareMessagesForDisplay(messages).map((entry) => entry.message);

  for (let messageIndex = displayMessages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const invocations = dedupToolInvocations(displayMessages[messageIndex]);
    for (let invocationIndex = invocations.length - 1; invocationIndex >= 0; invocationIndex -= 1) {
      const invocation = invocations[invocationIndex];
      const toolName = invocation.toolName.toLowerCase();
      if (toolName !== "update_plan" && toolName !== "write_todos") continue;

      const argsObj = asRecord(invocation.args);
      const resultObj = asRecord(invocation.result);
      const outputObj = asRecord(resultObj.output);
      const candidates = [
        outputObj.todos, outputObj.tasks, outputObj.plan,
        resultObj.todos, resultObj.tasks, resultObj.plan,
        argsObj.plan, argsObj.todos, argsObj.tasks,
      ];

      let steps: PlanStepState[] = [];
      for (const candidate of candidates) {
        steps = parsePlanSteps(candidate);
        if (steps.length > 0) break;
      }

      if (steps.length === 0) continue;

      const completedCount = steps.filter((step) => step.status === "completed").length;
      const inProgressCount = steps.filter((step) => step.status === "in_progress").length;
      const activeStep = steps.find((step) => step.status === "in_progress")?.step;
      const running = invocation.state !== "result" || inProgressCount > 0;

      return { steps, completedCount, inProgressCount, running, activeStep };
    }
  }

  return null;
}

// --- pipeline internals -----------------------------------------------------
// Exported so a custom chat UI (or the product) can reuse the building blocks of
// the row pipeline directly, instead of re-implementing tool/message accessors.
export {
  messageTimeMs,
  toolInvocationKey,
  preferToolInvocation,
  isLongRunningToolResult,
  messageRecord,
  contentRecord,
  metadataRecord,
  messageAgentRunId,
  messageFlag,
  isFinalAnswerMessage,
  isIntermediateAssistantMessage,
  normalizeAssistantDisplayText,
  hasMeaningfulTextPart,
  toolMessageKind,
  toolMessageName,
  toolMessageCallId,
  toolMessageInput,
  toolMessageOutput,
  messageHasToolActivity,
  finalAnswerRunIds,
  shouldConvertMessageToTraceNote,
  shouldFoldIntermediateMessage,
  completedTurnTraceDurations,
  messageWithTraceNote,
  messageWithMergedToolResult,
  messageWithRawToolCall,
  isCollapsibleAssistantMessage,
  assistantMessageHasRenderableContent,
  prepareMessagesForDisplay,
  isRunClosingMessage,
  completedRunLabel,
  rowSourceTimesMs,
  rowIsAfterIndex,
};

// --- markdown ---------------------------------------------------------------

/** Normalize compact/streamed assistant markdown for display (fixes table
 * alignment, heading spacing). Pure string transform. */
export function normalizeAssistantMarkdown(content: string): string {
  const trimmed = content.trim();
  const isCompactMarkdown = trimmed.split(/\r?\n/).length <= 2 && /(?:[ \t]---[ \t]|[ \t]#{1,6}\s|\|\s+\|)/.test(trimmed);
  const normalized = trimmed
    .replace(/[ \t]+---[ \t]+/g, "\n\n")
    .replace(/([.!?)\]])[ \t]+(?=#{1,6}\s)/g, "$1\n\n")
    .replace(/\|\s+\|/g, "|\n|")
    .replace(/\|\s+(?=\|?\s*:?-{3,})/g, "|\n")
    .replace(/(\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|)\s+/g, "$1\n");

  if (!isCompactMarkdown) return normalized;

  return normalized
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/(^|\n)([^|\n]{3,120}?)\s+(\|[^\n]+\|)(?=\n\|?\s*:?-{3,})/g, "$1$2\n\n$3");
}
