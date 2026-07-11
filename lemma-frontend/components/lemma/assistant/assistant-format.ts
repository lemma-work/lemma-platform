// Pure formatting / label / tool-payload helpers for the assistant UI. Extracted
// from assistant-experience.tsx so the giant component file holds JSX, not logic.
// No JSX, no hooks — just data → strings/labels. The display PIPELINE (dedup,
// rows, plan summary) lives in lemma-sdk; this is the product's presentation layer
// on top of it (tool display names, friendly statuses, payload summaries).
import {
  dedupToolInvocations,
  findPendingUserApprovalInvocation,
  formatDurationCompact,
  isRunClosingMessage,
  isToolInvocationActive,
  latestUserIndex,
  messageHasToolActivity,
  messageTextContent,
  messageTimeMs,
  preferToolInvocation,
  prepareMessagesForDisplay,
  rowIsAfterIndex,
  toolInvocationKey,
} from "lemma-sdk";
import type { AssistantMessagePart, AssistantRenderableMessage } from "lemma-sdk";
import {
  displayResourceLabel,
  extractDisplayResourceFromInvocation,
  isDisplayResourceToolName,
} from "@/lib/assistant/display-resource";
// Type-only import (erased at runtime, so no import cycle with the component).
import type {
  ActiveToolBanner,
  DisplayMessageRow,
  ToolCardArgs,
  ToolCardResult,
} from "./assistant-experience";

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

export function truncateLabel(value: string, max = 72): string {
  const trimmed = value.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

export function fileNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || normalized;
}

export function formatMessageTimestamp(createdAt?: Date): { text: string; dateTime: string } | null {
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

export function hasObjectEntries(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0;
}

export function formatCommandPreview(cmd: string): string {
  const compact = cmd.replace(/\s+/g, " ").trim();
  return truncateLabel(compact, 64);
}

export function primaryToolArgs(args: ToolCardArgs): ToolCardArgs {
  const request = asRecord(args.request);
  if (Object.keys(request).length > 0) return request;

  const waitConfig = asRecord(args.wait_config);
  if (Object.keys(waitConfig).length > 0) return waitConfig;

  return args;
}

export function toolArg(args: ToolCardArgs, key: string): unknown {
  const direct = args[key];
  if (typeof direct !== "undefined") return direct;
  return primaryToolArgs(args)[key];
}

const TOOL_DISPLAY_NAME_OVERRIDES: Record<string, string> = {
  apply_patch: "Applied patch",
  command_execution: "Terminal command",
  create_file: "Created file",
  edit_file: "Edited file",
  exec_command: "Terminal command",
  execute_command: "Terminal command",
  file_processor: "Analyzed files",
  grep: "Searched files",
  interact_subagent: "Sub-agent message",
  listen: "Transcribed audio",
  list_processes: "Workspace processes",
  manage_process: "Terminal process",
  pod_get_file_url: "File link",
  pod_get_records: "Read records",
  pod_list_files: "Listed files",
  pod_query: "Queried data",
  pod_read_file: "Read file",
  pod_search_files: "Searched files",
  pod_tables: "Inspected tables",
  pod_view_document_pages: "Viewed document",
  pod_write_record: "Wrote record",
  query_subagents: "Sub-agents",
  read_file: "Read file",
  say: "Generated speech",
  search_query: "Web search",
  search_tools: "Tool search",
  tool_search: "Tool search",
  spawn_subagent: "Spawned sub-agent",
  terminate_process: "Stopped process",
  view_image: "Viewed image",
  web_search: "Web search",
  write_stdin: "Terminal input",
  write_todos: "Updated plan",
};

function preserveCommonAcronyms(label: string): string {
  return label
    .replace(/\bApi\b/g, "API")
    .replace(/\bCli\b/g, "CLI")
    .replace(/\bCss\b/g, "CSS")
    .replace(/\bHtml\b/g, "HTML")
    .replace(/\bHttp\b/g, "HTTP")
    .replace(/\bId\b/g, "ID")
    .replace(/\bJson\b/g, "JSON")
    .replace(/\bSdk\b/g, "SDK")
    .replace(/\bSql\b/g, "SQL")
    .replace(/\bUi\b/g, "UI")
    .replace(/\bUrl\b/g, "URL");
}

export function formatToolDisplayName(toolName: string): string {
  const normalized = normalizeToolNameForDisplay(toolName);
  return TOOL_DISPLAY_NAME_OVERRIDES[normalized] || preserveCommonAcronyms(humanizeKey(normalized));
}

export function commentLabelFromArgs(args: ToolCardArgs): string | null {
  const comment = asString(toolArg(args, "comment"));
  return comment ? truncateLabel(comment, 72) : null;
}

export function toolCallPrimaryLabel(toolName: string, args: ToolCardArgs): string {
  const comment = commentLabelFromArgs(args);
  if (comment) return comment;

  const normalized = normalizeToolNameForDisplay(toolName);
  if (isCommandDetailTool(normalized)) {
    const cmd = firstToolArgString(args, ["cmd", "command"]);
    if (cmd) return `${formatToolDisplayName(toolName)} · ${formatCommandPreview(cmd)}`;
    const chars = asString(toolArg(args, "chars"));
    if (chars) return `${formatToolDisplayName(toolName)} · ${truncateLabel(chars.replace(/\s+/g, " ").trim(), 48)}`;
  }

  return formatToolDisplayName(toolName);
}

export function formatActiveToolSummary(toolName: string, args: ToolCardArgs): string {
  const lowerName = toolName.toLowerCase();
  const normalizedName = normalizeToolNameForDisplay(toolName);
  const comment = commentLabelFromArgs(args);

  if (isCommandDetailTool(normalizedName)) {
    if (comment) return `Running ${comment}`;
    const cmd = firstToolArgString(args, ["cmd", "command"]);
    return cmd ? `Running ${formatCommandPreview(cmd)}` : `Running ${formatToolDisplayName(toolName).toLowerCase()}`;
  }

  if (isDisplayResourceToolName(toolName)) {
    const displayResource = extractDisplayResourceFromInvocation({
      toolCallId: "display-resource-preview",
      toolName,
      args,
    });
    return displayResource ? `Showing ${displayResourceLabel(displayResource.request)}` : "Showing resource";
  }

  if (lowerName === "update_plan") {
    const plan = asArray(toolArg(args, "plan"));
    return `Updating plan (${plan.length} step${plan.length === 1 ? "" : "s"})`;
  }

  if (comment) return `Running ${comment}`;

  return `Running ${formatToolDisplayName(toolName)}`;
}

export function pathLabelFromToolArgs(args: ToolCardArgs): string | null {
  const candidates = [
    toolArg(args, "file_path"),
    toolArg(args, "filepath"),
    toolArg(args, "path"),
    toolArg(args, "target_file"),
    toolArg(args, "TargetFile"),
    toolArg(args, "AbsolutePath"),
  ];
  const path = candidates.find((value) => typeof value === "string" && value.trim().length > 0);
  return typeof path === "string" ? fileNameFromPath(path.trim()) : null;
}

export function patchFileLabel(args: ToolCardArgs): string | null {
  const patch = asString(toolArg(args, "patch")) || asString(toolArg(args, "input"));
  if (!patch) return null;
  const match = /^\*\*\* (?:Update|Add|Delete) File: (.+)$/m.exec(patch);
  return match?.[1] ? fileNameFromPath(match[1].trim()) : null;
}

export function commandPathLabel(cmd: string): string | null {
  const matches = [...cmd.matchAll(/(?:^|\s)(?:\.{0,2}\/|~\/|\/)?[A-Za-z0-9_.@/+:-]+\.[A-Za-z0-9]+(?=\s|$|:)/g)];
  const candidate = matches[matches.length - 1]?.[0]?.trim();
  return candidate ? fileNameFromPath(candidate) : null;
}

export function formatFriendlyCommandStatus(cmd: string): string | null {
  const compact = cmd.replace(/\s+/g, " ").trim();
  const lower = compact.toLowerCase();
  const pathLabel = commandPathLabel(compact);

  if (/^(?:npx\s+)?rg(?:\s|$)/.test(lower) || lower.includes(" ripgrep ")) {
    return "Searching";
  }

  if (/^(?:nl|sed|cat|head|tail|less)(?:\s|$)/.test(lower)) {
    return pathLabel ? `Reading ${pathLabel}` : "Reading file";
  }

  if (/^git status(?:\s|$)/.test(lower)) return "Checking git status";
  if (/^git diff(?:\s|$)/.test(lower)) return "Reviewing changes";
  if (/^git show(?:\s|$)/.test(lower)) return "Inspecting commit";
  if (/^(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|check|lint|typecheck|build)(?:\s|$)/.test(lower)) {
    return "Running checks";
  }

  return null;
}

export function formatFriendlyToolStatus(toolName: string, args: ToolCardArgs): string | null {
  const lowerName = toolName.toLowerCase();
  const comment = commentLabelFromArgs(args);
  if (comment) return comment;

  if (lowerName === "exec_command" || lowerName.includes("command") || lowerName.includes("shell")) {
    const cmd = asString(toolArg(args, "cmd"));
    return cmd ? formatFriendlyCommandStatus(cmd) : null;
  }

  if (isDisplayResourceToolName(toolName)) {
    const displayResource = extractDisplayResourceFromInvocation({
      toolCallId: "display-resource-preview",
      toolName,
      args,
    });
    return displayResource ? `Showing ${displayResourceLabel(displayResource.request)}` : "Showing resource";
  }

  if (lowerName === "update_plan" || lowerName === "write_todos") return "Updating plan";
  if (lowerName === "say") return "Generating speech";
  if (lowerName === "listen") return "Transcribing audio";
  if (lowerName === "spawn_subagent") return "Spawning sub-agent";
  if (lowerName === "interact_subagent" || lowerName === "query_subagents") return "Coordinating sub-agents";
  if (lowerName === "search_tools" || lowerName === "tool_search") return "Searching tools";
  if (lowerName === "pod_query" || lowerName === "pod_get_records") return "Reading pod data";
  if (lowerName === "pod_write_record") return "Writing pod data";
  if (lowerName.startsWith("pod_")) return "Working with pod data";

  const pathLabel = pathLabelFromToolArgs(args) || patchFileLabel(args);
  if (lowerName.includes("apply_patch") || lowerName.includes("write") || lowerName.includes("edit")) {
    return pathLabel ? `Editing ${pathLabel}` : "Editing file";
  }
  if (lowerName.includes("read") || lowerName.includes("open") || lowerName.includes("file")) {
    return pathLabel ? `Reading ${pathLabel}` : "Reading file";
  }
  if (lowerName.includes("search") || lowerName.includes("find") || lowerName.includes("rg")) {
    return "Searching";
  }

  return null;
}

export function objectPayload(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

export function normalizeToolParts(parts: AssistantMessagePart[]): AssistantMessagePart[] {
  const normalized: AssistantMessagePart[] = [];
  const toolIndexes = new Map<string, number>();

  parts.forEach((part) => {
    if (part.type !== "tool") {
      normalized.push(part);
      return;
    }

    const key = toolInvocationKey(part.toolInvocation);
    const existingIndex = toolIndexes.get(key);
    if (typeof existingIndex === "number") {
      const existing = normalized[existingIndex];
      if (existing?.type === "tool") {
        normalized[existingIndex] = {
          ...existing,
          toolInvocation: preferToolInvocation(existing.toolInvocation, part.toolInvocation),
        };
      }
      return;
    }

    toolIndexes.set(key, normalized.length);
    normalized.push(part);
  });

  return normalized;
}

export function currentRunPendingApproval(rows: DisplayMessageRow[], latestUser: number): boolean {
  return !!findPendingUserApprovalInvocation(rows, latestUser);
}

export function currentRunStatusLabel({
  messages,
  rows,
  isConversationBusy,
  nowMs,
}: {
  messages: AssistantRenderableMessage[];
  rows: DisplayMessageRow[];
  isConversationBusy: boolean;
  nowMs: number;
}): { label: string; shimmer: boolean } | null {
  const latestUser = latestUserIndex(messages);

  // A pending question/approval is a waiting state that outlives the run: the
  // run ends (conversation -> WAITING) while the card waits for the user, so
  // surface it even when the conversation is no longer "busy".
  if (currentRunPendingApproval(rows, latestUser)) {
    return { label: "Waiting for your input", shimmer: false };
  }

  if (!isConversationBusy) return null;

  const currentAssistantRows = rows.filter((row) => row.message.role === "assistant" && rowIsAfterIndex(row, latestUser));
  const currentMessages = messages.slice(latestUser + 1);
  const assistantMessages = currentMessages.filter((message) => message.role === "assistant");
  const hasClosingAnswer = assistantMessages.some(isRunClosingMessage);
  const hasAssistantText = assistantMessages.some((message) => messageTextContent(message).length > 0);
  const hasToolActivity = assistantMessages.some(messageHasToolActivity);

  if (currentAssistantRows.length === 0) {
    return { label: "Thinking", shimmer: true };
  }

  if (assistantMessages.length > 0 && hasAssistantText && hasToolActivity && !hasClosingAnswer) {
    const startMs = assistantMessages
      .filter(messageHasToolActivity)
      .map(messageTimeMs)
      .find((value): value is number => value !== null);
    if (startMs) {
      return { label: `Working for ${formatDurationCompact(Math.max(1000, nowMs - startMs))}`, shimmer: false };
    }
  }

  return null;
}

export function currentToolStatusLabel({
  messages,
  isConversationBusy,
  streamingTool,
}: {
  messages: AssistantRenderableMessage[];
  isConversationBusy: boolean;
  streamingTool?: { toolCallId?: string; toolName: string; args?: Record<string, unknown> } | null;
}): InlineToolStatus | null {
  if (!isConversationBusy) return null;

  if (streamingTool) {
    const friendlyLabel = formatFriendlyToolStatus(streamingTool.toolName, streamingTool.args || {});
    return {
      label: friendlyLabel || formatActiveToolSummary(streamingTool.toolName, streamingTool.args || {}),
      shimmer: true,
      toolCallId: streamingTool.toolCallId,
      toolName: streamingTool.toolName,
    };
  }

  const latestUser = latestUserIndex(messages);
  const currentAssistantMessages = messages
    .slice(latestUser + 1)
    .filter((message) => message.role === "assistant");

  for (let messageIndex = currentAssistantMessages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const invocations = dedupToolInvocations(currentAssistantMessages[messageIndex]);
    for (let invocationIndex = invocations.length - 1; invocationIndex >= 0; invocationIndex -= 1) {
      const invocation = invocations[invocationIndex];
      if (isToolInvocationActive(invocation)) {
        const friendlyLabel = formatFriendlyToolStatus(invocation.toolName, invocation.args);
        return {
          label: friendlyLabel || formatActiveToolSummary(invocation.toolName, invocation.args),
          shimmer: true,
          toolCallId: invocation.toolCallId,
          toolName: invocation.toolName,
        };
      }
    }
  }

  return { label: "Thinking", shimmer: true };
}

export interface InlineToolStatus {
  label: string;
  shimmer: boolean;
  toolCallId?: string;
  toolName?: string;
}

/**
 * The streaming status is a low-latency placeholder. Once the same call is in
 * the current run's durable rows, that row owns the UI so the handoff happens
 * in one render without briefly showing both representations.
 */
export function isInlineToolStatusAlreadyVisible({
  rows,
  latestUser,
  status,
}: {
  rows: DisplayMessageRow[];
  latestUser: number;
  status: InlineToolStatus | null | undefined;
}): boolean {
  if (!status) return false;

  for (let rowIndex = rows.length - 1; rowIndex >= 0; rowIndex -= 1) {
    const row = rows[rowIndex];
    if (!rowIsAfterIndex(row, latestUser)) continue;

    const invocations = dedupToolInvocations(row.message);
    for (let invocationIndex = invocations.length - 1; invocationIndex >= 0; invocationIndex -= 1) {
      const invocation = invocations[invocationIndex];
      if (status.toolCallId && invocation.toolCallId === status.toolCallId) return true;

      // Partial stream tokens may expose the tool name before the call id. In
      // that short window, an active row with the same name is the same handoff
      // candidate; completed rows do not suppress a genuinely new call.
      if (
        !status.toolCallId
        && status.toolName
        && invocation.toolName === status.toolName
        && isToolInvocationActive(invocation)
      ) {
        return true;
      }
    }
  }

  return false;
}

export function getActiveToolBanner(messages: AssistantRenderableMessage[]): ActiveToolBanner | null {
  const displayMessages = prepareMessagesForDisplay(messages).map((entry) => entry.message);

  for (let i = displayMessages.length - 1; i >= 0; i -= 1) {
    const message = displayMessages[i];
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

export function reasoningPartLabel(isStreaming: boolean, durationMs?: number): string {
  if (isStreaming) return "Thinking";
  return `Thought${durationMs ? ` for ${Math.max(1, Math.round(durationMs / 1000))}s` : ""}`;
}

export function formatToolDetailValue(value: unknown): string {
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

export function humanizeKey(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function isToolPayloadValueMeaningful(value: unknown): boolean {
  if (value === null || typeof value === "undefined") return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(asRecord(value)).length > 0;
  return true;
}

export function summarizeToolPayload(
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

export function countSummarizablePayloadEntries(
  payload: Record<string, unknown>,
  options?: { excludeKeys?: string[] },
): number {
  const excluded = new Set((options?.excludeKeys || []).map((key) => key.toLowerCase()));
  return Object.entries(payload)
    .filter(([key, value]) => !excluded.has(key.toLowerCase()) && isToolPayloadValueMeaningful(value))
    .length;
}

export function pickPreferredEntries(
  entries: Array<{ key: string; value: string }>,
  preferredKeys: string[],
  max: number,
): Array<{ key: string; value: string }> {
  const preferredSet = new Set(preferredKeys.map((key) => key.toLowerCase()));
  const preferred = entries.filter((entry) => preferredSet.has(entry.key.toLowerCase()));
  const rest = entries.filter((entry) => !preferredSet.has(entry.key.toLowerCase()));
  return [...preferred, ...rest].slice(0, max);
}

export function normalizeToolNameForDisplay(toolName: string): string {
  const normalized = toolName
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[.\-:\s]+/g, "_");
  return normalized.startsWith("lemma_") ? normalized.slice("lemma_".length) : normalized;
}

function normalizedPayloadKey(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[.\-:\s]+/g, "_");
}

export function payloadValue(record: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (typeof record[key] !== "undefined") return record[key];
  }

  const normalizedKeys = new Set(keys.map(normalizedPayloadKey));
  const matchedEntry = Object.entries(record).find(([key]) => normalizedKeys.has(normalizedPayloadKey(key)));
  return matchedEntry?.[1];
}

export function toolStatusLabel(state: string, result: ToolCardResult): { label: string; tone: "running" | "success" | "error" } {
  const exitCode = payloadValue(result, ["exit_code", "exitCode"]);
  const status = asString(payloadValue(result, ["status"]));
  const hasLiveProcess = (
    typeof payloadValue(result, ["process_id", "processId"]) === "string"
    || typeof payloadValue(result, ["session_id", "sessionId"]) === "string"
  ) && exitCode == null && status !== "completed";

  if (state !== "result" || result.completed === false || hasLiveProcess) {
    return { label: "Running", tone: "running" };
  }

  if (result.success === false || typeof result.error === "string" || status === "failed") {
    return { label: "Failed", tone: "error" };
  }

  return { label: "Complete", tone: "success" };
}

export function compactCommand(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function firstRecordString(record: Record<string, unknown>, keys: string[]): string | undefined {
  return asString(payloadValue(record, keys));
}

export function firstToolArgString(args: ToolCardArgs, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = asString(toolArg(args, key));
    if (value) return value;
  }
  return undefined;
}

export function formatJsonPreview(value: unknown, max = 3000): string {
  if (typeof value === "string") return value.trim();
  try {
    return JSON.stringify(value, null, 2).slice(0, max);
  } catch {
    return String(value).slice(0, max);
  }
}

export function resultText(result: ToolCardResult, keys: string[]): string | undefined {
  const value = payloadValue(result, keys);
  if (typeof value === "string" && value.trim()) return value.trim();
  if (value && typeof value === "object") return formatJsonPreview(value, 2400);
  return undefined;
}

export function parsePatchFileEntries(patch: string): Array<{ action: string; path: string }> {
  const entries: Array<{ action: string; path: string }> = [];
  const filePattern = /^\*\*\* (Add|Update|Delete) File: (.+)$/gm;
  let match: RegExpExecArray | null;
  while ((match = filePattern.exec(patch)) !== null) {
    entries.push({ action: match[1], path: match[2].trim() });
  }
  return entries;
}

export function countPatchLines(patch: string): { added: number; removed: number } {
  return patch.split("\n").reduce((counts, line) => {
    if (line.startsWith("+") && !line.startsWith("+++")) counts.added += 1;
    if (line.startsWith("-") && !line.startsWith("---")) counts.removed += 1;
    return counts;
  }, { added: 0, removed: 0 });
}

export function operationRecordsFromResult(result: ToolCardResult): Record<string, unknown>[] {
  const candidateArrays = [
    result.results,
    result.items,
    result.operations,
    result.records,
    asRecord(result.data).results,
    asRecord(result.data).items,
    asRecord(result.data).operations,
  ];

  for (const candidate of candidateArrays) {
    const rows = asArray(candidate)
      .map(asRecord)
      .filter((record) => Object.keys(record).length > 0);
    if (rows.length > 0) return rows;
  }

  return [];
}

/** First non-empty array of objects under any of `keys`, checking the record
 * root and a nested `data` envelope (tool results wrap rows either way). */
export function firstArrayOfRecords(record: Record<string, unknown>, keys: string[]): Record<string, unknown>[] {
  const pools = [record, asRecord(payloadValue(record, ["data"]))];
  for (const pool of pools) {
    for (const key of keys) {
      const rows = asArray(payloadValue(pool, [key])).map(asRecord).filter((entry) => Object.keys(entry).length > 0);
      if (rows.length) return rows;
    }
  }
  return [];
}

export type TodoItemState = "done" | "active" | "todo";
export interface TodoItem { state: TodoItemState; text: string; }

/** Normalize a `write_todos` / `update_plan` invocation into checklist items,
 * accepting either structured task objects or markdown checklist lines. */
export function parseTodoItems(args: Record<string, unknown>, result: Record<string, unknown>): TodoItem[] {
  const structured = firstArrayOfRecords(result, ["todos", "tasks", "items", "plan"]).length
    ? firstArrayOfRecords(result, ["todos", "tasks", "items", "plan"])
    : firstArrayOfRecords(args, ["todos", "tasks", "plan"]);

  if (structured.length) {
    const mapped = structured
      .map<TodoItem>((entry) => {
        const text = firstRecordString(entry, ["content", "text", "title", "task", "step", "label", "name"]) || "";
        const statusRaw = (firstRecordString(entry, ["status", "state"]) || "").toLowerCase();
        const done = statusRaw === "completed" || statusRaw === "done" || entry.done === true || entry.completed === true;
        const active = statusRaw === "in_progress" || statusRaw === "active" || statusRaw === "running";
        return { state: done ? "done" : active ? "active" : "todo", text };
      })
      .filter((item) => item.text);
    if (mapped.length) return mapped;
  }

  // Markdown checklist lines: "- [ ] todo" / "- [x] done" / "- [~] in-progress".
  return asArray(toolArg(args, "todos"))
    .map((value) => String(value))
    .map<TodoItem>((line) => {
      const match = /^\s*[-*]?\s*\[([ xX~\-])\]\s*(.*)$/.exec(line);
      if (match) {
        const mark = match[1].toLowerCase();
        return { state: mark === "x" ? "done" : mark === "~" || mark === "-" ? "active" : "todo", text: match[2].trim() };
      }
      return { state: "todo", text: line.replace(/^\s*[-*]\s*/, "").trim() };
    })
    .filter((item) => item.text);
}

export function readableToolEntityName(normalizedName: string, prefix: "function_" | "agent_"): string {
  return humanizeKey(normalizedName.slice(prefix.length) || prefix.replace("_", ""));
}

export function isCommandDetailTool(normalizedName: string): boolean {
  return normalizedName === "exec_command"
    || normalizedName === "execute_command"
    || normalizedName === "command_execution"
    || normalizedName === "manage_process"
    || normalizedName === "write_stdin"
    || normalizedName === "terminate_process"
    || normalizedName === "list_processes";
}

export function stringifyAssistantError(error: unknown): string {
  if (!error) return "";
  if (error instanceof Error) return error.message || error.name;
  if (typeof error === "string") return error;

  try {
    return JSON.stringify(error, null, 2);
  } catch {
    return String(error);
  }
}
