import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LemmaClient } from "../client.js";
import type {
  AgentRuntimeConfig,
  AvailableModelInfo,
  Conversation,
  ConversationMessage,
  ConversationModel,
  FileResponse,
  MessageKind,
} from "../types.js";
import { useAssistantRuntime } from "./useAssistantRuntime.js";
import { useAssistantSession, type AssistantStreamingTool } from "./useAssistantSession.js";

export type { AssistantStreamingTool } from "./useAssistantSession.js";

export interface AssistantConversationScope {
  podId?: string | null;
  agentName?: string | null;
  /**
   * @deprecated Use agentName instead.
   */
  assistantName?: string | null;
  /**
   * @deprecated Use agentName instead.
   */
  assistantId?: string | null;
  organizationId?: string | null;
}

// These renderable-message types now live in the framework-agnostic core so the
// display pipeline can share them; imported for local use and re-exported so
// existing `lemma-sdk/react` imports keep working.
import type {
  AssistantToolInvocation,
  AssistantMessagePart,
  AssistantRenderableMessage,
} from "../core/agent/renderable.js";
export type {
  AssistantToolInvocation,
  AssistantMessagePart,
  AssistantRenderableMessage,
};

export interface AssistantAction {
  id: string;
  type: "tool_call" | "message" | "thinking";
  status: "pending" | "executing" | "completed" | "failed";
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  result?: unknown;
  error?: string;
  timestamp: Date;
}

export type AssistantPendingFileUploadStatus = "queued" | "uploading" | "uploaded" | "failed";

export interface AssistantPendingFileUpload {
  key: string;
  file: File;
  status: AssistantPendingFileUploadStatus;
  path?: string;
  error?: string;
}

export interface UseAssistantControllerOptions extends AssistantConversationScope {
  client: LemmaClient;
  enabled?: boolean;
  autoLoad?: boolean;
  instructions?: string | null;
  autoLoadMessages?: boolean;
  /**
   * Which conversations the history holds. `'pod'` is every conversation in the
   * pod, so a chat surface that switches agents keeps one continuous list.
   * `'agent'` lists only what this agent ran — what a per-agent surface such as
   * the test panel means by "history". Scoping happens on the server, so the
   * page size counts this agent's runs rather than the pod's.
   */
  historyScope?: "pod" | "agent";
}

export interface SendAssistantControllerMessageOptions {
  forceNewConversation?: boolean;
  metadata?: Record<string, unknown> | null;
  conversationMetadata?: Record<string, unknown> | null;
  instructions?: string | null;
}

export type AssistantUserApprovalDecision = "APPROVE_ONCE" | "APPROVE_FOR_SESSION" | "DENY";

export interface UseAssistantControllerResult {
  messages: AssistantRenderableMessage[];
  conversations: Conversation[];
  openedConversationId: string | null;
  activeConversationId: string | null;
  availableModels: AvailableModelInfo[];
  conversationModel: ConversationModel | null;
  conversationRuntime: AgentRuntimeConfig | null;
  isOpenedConversationRunning: boolean;
  isActiveConversationRunning: boolean;
  isLoading: boolean;
  isLoadingConversations: boolean;
  isLoadingMoreConversations: boolean;
  hasMoreConversations: boolean;
  isLoadingMessages: boolean;
  isLoadingOlderMessages: boolean;
  hasOlderMessages: boolean;
  isUploadingFiles: boolean;
  pendingFiles: File[];
  pendingFileUploads: AssistantPendingFileUpload[];
  error: string | null;
  canRetryFailedMessage: boolean;
  pendingActions: AssistantAction[];
  completedActions: AssistantAction[];
  streamingTool: AssistantStreamingTool | null;
  openConversation: (conversationId: string) => void;
  closeConversation: () => void;
  selectConversation: (conversationId: string | null) => void;
  setConversationModel: (model: ConversationModel | null, runtime?: AgentRuntimeConfig | null) => Promise<void>;
  sendMessage: (content: string, options?: SendAssistantControllerMessageOptions) => Promise<void>;
  /**
   * Append a follow-up message to a conversation that already has a run in
   * flight, instead of starting a new one. Unlike `sendMessage`, this never
   * opens its own SSE stream — it persists the message and reattaches
   * whatever stream should be watching the conversation, relying on that
   * stream (or the harness's own follow-up-run backstop) to surface the
   * result. Requires an already-open/active conversation.
   */
  steerMessage: (content: string, options?: SendAssistantControllerMessageOptions) => Promise<void>;
  retryFailedMessage: () => Promise<void>;
  uploadFiles: (files: File[], options?: { deferUntilSend?: boolean }) => Promise<void>;
  removePendingFile: (fileKey: string) => void;
  clearPendingFiles: () => void;
  loadOlderMessages: () => Promise<boolean>;
  loadMoreConversations: () => Promise<Conversation[]>;
  resolveUserApproval: (
    approvalId: string,
    decision: AssistantUserApprovalDecision,
    response?: Record<string, unknown> | null,
  ) => Promise<void>;
  clearMessages: () => void;
  stop: () => void;
}

interface AssistantMessageMetadata {
  tool_name?: string;
  message_type?: "tool_call" | "tool_return";
  tool_call_id?: string;
  args?: Record<string, unknown>;
  result?: {
    success?: boolean;
    message?: string;
    error?: string | null;
    [key: string]: unknown;
  };
}

type AssistantApiConversationMessage = ConversationMessage & {
  conversation_id?: string;
  /** Set by the runtime store when this message replaced a provisional turn. */
  optimistic_id?: string;
  metadata?: (Record<string, unknown> & AssistantMessageMetadata) | null;
  message_metadata?: AssistantMessageMetadata;
  tool_calls?: Record<string, unknown>[];
};

const CONVERSATIONS_PAGE_SIZE = 30;

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function parseMaybeJsonObject(value: unknown): Record<string, unknown> {
  if (isRecord(value)) return value;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return isRecord(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }
  return {};
}

function parseMaybeJsonValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function parseTimestampMs(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const timestamp = new Date(value).getTime();
    if (Number.isFinite(timestamp) && timestamp > 0) {
      return timestamp;
    }
  }
  return null;
}

function parseDurationMs(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return Math.round(value);
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) {
      return Math.round(parsed);
    }
  }
  return undefined;
}

function getFileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function parseThinkingDurationFromRecord(record: Record<string, unknown>): number | undefined {
  return parseDurationMs(record.duration_ms)
    ?? parseDurationMs(record.durationMs)
    ?? parseDurationMs(record.elapsed_ms)
    ?? parseDurationMs(record.elapsedMs)
    ?? parseDurationMs(record.thought_duration_ms)
    ?? parseDurationMs(record.thoughtDurationMs);
}

function extractThinkingPart(msg: AssistantApiConversationMessage): {
  text: string;
  state: "streaming" | "done";
  durationMs?: number;
} | null {
  if (msg.kind !== "THINKING") return null;

  const text = typeof msg.text === "string" ? msg.text.trim() : "";
  if (!text) return null;

  const metadata = getMessageMetadata(msg);
  return {
    text,
    state: "done",
    durationMs: metadata
      ? parseThinkingDurationFromRecord(metadata as Record<string, unknown>)
      : undefined,
  };
}

export interface HeldStreamingThinking {
  conversationId: string;
  text: string;
}

/** Bridge the streamed thought to its durable message.
 *
 * A thought arrives twice: as `thinking` tokens, and again as a durable
 * `THINKING` message. The session clears the token buffer the moment that
 * message upserts, but the runtime mirrors session messages through an effect,
 * so the durable row is one commit behind. Without a bridge the reasoning row
 * blanks in that window - and an empty run is exactly what makes the run-status
 * placeholder flash its own "Thinking" into the gap, which reads as two
 * competing indicators.
 *
 * So keep showing the last streamed thought until its durable message actually
 * lands, the run ends, or the conversation changes. */
export function resolveStreamingThinking({
  held,
  conversationId,
  streamed,
  messages,
  isRunning,
}: {
  held: { current: HeldStreamingThinking | null };
  conversationId: string;
  streamed: string;
  messages: AssistantApiConversationMessage[];
  isRunning: boolean;
}): string {
  if (streamed.length > 0) {
    held.current = { conversationId, text: streamed };
    return streamed;
  }

  const pending = held.current;
  if (!pending || pending.conversationId !== conversationId || !isRunning) {
    held.current = null;
    return "";
  }

  // The durable text is the streamed buffer plus whatever the model emitted
  // around it, so match by containment rather than equality. Prefix is the
  // shape of a buffer that was filled from the run's first token; a buffer
  // filled by a *resumed* stream starts wherever we reattached, which is a
  // suffix. Both are runs of tokens out of the same message, so both are
  // inside it.
  const durableLanded = messages.some((message) => (
    message.kind === "THINKING"
    && typeof message.text === "string"
    && message.text.trim().includes(pending.text)
  ));
  if (durableLanded) {
    held.current = null;
    return "";
  }
  return pending.text;
}

export interface HeldStreamingText {
  conversationId: string;
  text: string;
}

/** Bridge the streamed answer to its durable message.
 *
 * The same one-commit gap `resolveStreamingThinking` covers, with one rule
 * changed: a thought that outlives its run is noise, so that bridge drops on
 * `!isRunning`. An *answer* that outlives its run is the answer. Dropping it
 * there is what made a reply type itself out in front of the reader and then
 * vanish the moment the turn settled — with the text sitting in the database
 * the whole time, which is why reloading brought it back.
 *
 * The frame can genuinely go missing: publishing is best-effort and the
 * realtime fan-out drops a subscriber that falls behind. The session reconciles
 * that with one list when it sees a buffer nothing claimed; this keeps the
 * words on screen until they do land, so the recovery is invisible rather than
 * a blank turn followed by a reappearance.
 *
 * Bounded by the things that make the buffer meaningless rather than by time:
 * the durable message landing, the conversation changing, a new turn being
 * sent, or the run ending in a failure that is now the truer thing to show.
 */
export function resolveStreamingText({
  held,
  conversationId,
  streamed,
  messages,
  failed,
}: {
  held: { current: HeldStreamingText | null };
  conversationId: string;
  streamed: string;
  messages: AssistantApiConversationMessage[];
  failed: boolean;
}): string {
  if (streamed.length > 0) {
    held.current = { conversationId, text: streamed };
    return streamed;
  }

  const pending = held.current;
  if (!pending || pending.conversationId !== conversationId || failed) {
    held.current = null;
    return "";
  }

  // Backwards: the message this is waiting for is the last thing the run wrote,
  // and in the steady state there is no pending buffer to scan for at all.
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "assistant" || message.kind === "THINKING") continue;
    if (typeof message.text !== "string") continue;
    // Containment, not equality: the durable text is the buffer plus whatever
    // the model emitted around it. Prefix was too narrow by exactly the case
    // this bridge is most needed in — coming back to a run already in flight.
    // A resumed stream starts at the token after we reattached, so its buffer
    // is the *end* of the answer, and "The report is ready." does not start
    // with "is ready.". The buffer was never retired, and the fragment stayed
    // on screen underneath the finished answer.
    if (message.text.trim().includes(pending.text)) {
      held.current = null;
      return "";
    }
  }
  return pending.text;
}

function normalizeToolResult(value: unknown): Record<string, unknown> {
  if (isRecord(value)) return value;
  if (Array.isArray(value)) return { output: value };
  if (typeof value === "undefined" || value === null) return {};
  return { output: value };
}

function getMessageMetadata(msg: AssistantApiConversationMessage): AssistantMessageMetadata | undefined {
  return (msg.message_metadata || msg.metadata || undefined) as AssistantMessageMetadata | undefined;
}

function getNativeToolPayload(msg: AssistantApiConversationMessage): {
  kind: "call" | "result";
  toolCallId: string;
  toolName?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
} | null {
  const toolName = typeof msg.tool_name === "string" ? msg.tool_name : undefined;

  if (msg.kind === "TOOL_CALL") {
    return {
      kind: "call",
      toolCallId: (typeof msg.tool_call_id === "string" && msg.tool_call_id) || `${msg.id}-tool-call`,
      toolName,
      args: parseMaybeJsonObject(parseMaybeJsonValue(msg.tool_args)),
    };
  }

  if (msg.kind === "TOOL_RETURN") {
    return {
      kind: "result",
      toolCallId: (typeof msg.tool_call_id === "string" && msg.tool_call_id) || `${msg.id}-tool-result`,
      toolName,
      result: normalizeToolResult(msg.tool_result),
    };
  }

  return null;
}

function toolInvocationKey(tool: AssistantToolInvocation): string {
  return `${tool.toolCallId}:${tool.state}`;
}

function mapToolInvocations(msg: AssistantApiConversationMessage): AssistantToolInvocation[] {
  const invocations: AssistantToolInvocation[] = [];
  const metadata = getMessageMetadata(msg);
  const nativeToolPayload = getNativeToolPayload(msg);

  if (metadata?.message_type === "tool_call") {
    invocations.push({
      toolCallId: metadata.tool_call_id || `${msg.id}-tool-call`,
      toolName: metadata.tool_name || "tool",
      args: metadata.args || {},
      state: "call",
    });
  }

  if (metadata?.message_type === "tool_return") {
    invocations.push({
      toolCallId: metadata.tool_call_id || `${msg.id}-tool-result`,
      toolName: metadata.tool_name || "tool",
      args: metadata.args || {},
      state: "result",
      result: metadata.result as Record<string, unknown> | undefined,
    });
  }

  if (Array.isArray(msg.tool_calls)) {
    msg.tool_calls.forEach((rawTool, index) => {
      const tool = isRecord(rawTool) ? rawTool : {};
      const fn = isRecord(tool.function) ? tool.function : {};
      const toolName = (
        (typeof fn.name === "string" && fn.name)
        || (typeof tool.tool_name === "string" && tool.tool_name)
        || (typeof tool.name === "string" && tool.name)
        || "tool"
      );
      const argsRaw = fn.arguments ?? tool.args ?? tool.arguments ?? tool.input;
      invocations.push({
        toolCallId:
          (typeof tool.id === "string" && tool.id)
          || (typeof tool.tool_call_id === "string" && tool.tool_call_id)
          || `${msg.id}-tool-${index}`,
        toolName,
        args: parseMaybeJsonObject(argsRaw),
        state: "call",
      });
    });
  }

  if (nativeToolPayload?.kind === "call") {
    invocations.push({
      toolCallId: nativeToolPayload.toolCallId,
      toolName: nativeToolPayload.toolName || metadata?.tool_name || "tool",
      args: nativeToolPayload.args || metadata?.args || {},
      state: "call",
    });
  }

  if (nativeToolPayload?.kind === "result") {
    invocations.push({
      toolCallId: nativeToolPayload.toolCallId,
      toolName: nativeToolPayload.toolName || metadata?.tool_name || "tool",
      args: metadata?.args || {},
      state: "result",
      result: nativeToolPayload.result || {},
    });
  }

  const seen = new Set<string>();
  return invocations.filter((invocation) => {
    const key = toolInvocationKey(invocation);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function mapConversationMessage(
  msg: AssistantApiConversationMessage,
): AssistantRenderableMessage {
  const toolInvocations = mapToolInvocations(msg);
  const createdAtMs = parseTimestampMs(msg.created_at) ?? undefined;
  const parts: AssistantMessagePart[] = [];
  let content = "";

  // Flat shape: a message is exactly one kind. Thinking renders as a reasoning
  // part; text/notification render as a text part; tool_call/tool_return render
  // via toolInvocations below.
  const thinkingPart = extractThinkingPart(msg);
  if (thinkingPart) {
    parts.push({
      id: `${msg.id}-reasoning`,
      type: "reasoning",
      text: thinkingPart.text,
      state: thinkingPart.state,
      durationMs: thinkingPart.durationMs,
      startedAtMs: createdAtMs,
    });
  } else if (msg.kind === "TEXT" || msg.kind === "NOTIFICATION") {
    content = typeof msg.text === "string" ? msg.text.trim() : "";
    if (content) {
      parts.push({
        id: `${msg.id}-text`,
        type: "text",
        text: content,
      });
    }
  }

  toolInvocations.forEach((toolInvocation, index) => {
    parts.push({
      id: `${msg.id}-tool-${index}`,
      type: "tool",
      toolInvocation,
    });
  });

  return {
    id: msg.id,
    role: msg.role === "user" ? "user" : "assistant",
    content,
    toolInvocations,
    parts,
    createdAt: msg.created_at ? new Date(msg.created_at) : new Date(),
    conversation_id: msg.conversation_id,
    optimistic_id: msg.optimistic_id,
    sequence: msg.sequence,
    agent_run_id: msg.agent_run_id,
    metadata: msg.metadata ?? null,
    message_metadata: (msg.message_metadata as Record<string, unknown> | undefined) ?? null,
    kind: msg.kind,
    tool_call_id: msg.tool_call_id ?? null,
    tool_name: msg.tool_name ?? null,
    tool_args: msg.tool_args ?? null,
    tool_result: msg.tool_result ?? null,
  };
}

// Exported for tests. Consumers must not re-merge tool returns on top of this:
// a TOOL_RETURN is already folded into its originating TOOL_CALL below, so a
// second pass finds invocations that are already `state: "result"` and rewrites
// them to the values they hold — producing fresh object identities on every
// render for no gain.
export function mapConversationMessages(messages: AssistantApiConversationMessage[]): AssistantRenderableMessage[] {
  const mappedMessages: AssistantRenderableMessage[] = [];
  const pendingToolCalls = new Map<string, AssistantToolInvocation>();

  messages.forEach((rawMessage) => {
    const mappedMessage = mapConversationMessage(rawMessage);

    mappedMessage.toolInvocations?.forEach((invocation) => {
      if (invocation.state === "call") {
        pendingToolCalls.set(invocation.toolCallId, invocation);
      }
    });

    const nativePayload = getNativeToolPayload(rawMessage);
    const isToolRole = rawMessage.role === "tool";

    if (isToolRole && nativePayload?.kind === "result" && mappedMessage.toolInvocations && mappedMessage.toolInvocations.length > 0) {
      let mergedIntoPriorCall = false;

      mappedMessage.toolInvocations.forEach((resultInvocation) => {
        if (resultInvocation.state !== "result") return;
        const pendingInvocation = pendingToolCalls.get(resultInvocation.toolCallId);
        if (!pendingInvocation) return;

        pendingInvocation.state = "result";
        pendingInvocation.result = resultInvocation.result || {};
        if (pendingInvocation.toolName === "tool" && resultInvocation.toolName !== "tool") {
          pendingInvocation.toolName = resultInvocation.toolName;
        }
        mergedIntoPriorCall = true;
      });

      if (mergedIntoPriorCall) {
        return;
      }
    }

    if (mappedMessage.toolInvocations) {
      mappedMessage.toolInvocations.forEach((invocation) => {
        if (invocation.state === "result") {
          const pendingInvocation = pendingToolCalls.get(invocation.toolCallId);
          if (pendingInvocation) {
            if ((invocation.toolName === "tool" || !invocation.toolName) && pendingInvocation.toolName) {
              invocation.toolName = pendingInvocation.toolName;
            }
            if (Object.keys(invocation.args).length === 0 && Object.keys(pendingInvocation.args).length > 0) {
              invocation.args = pendingInvocation.args;
            }
          }
        }
      });
    }

    mappedMessages.push(mappedMessage);
  });

  return mappedMessages;
}

function sortConversationsByUpdatedAt(conversations: Conversation[]): Conversation[] {
  return [...conversations].sort((a, b) => {
    const aTime = new Date(a.updated_at || a.created_at).getTime();
    const bTime = new Date(b.updated_at || b.created_at).getTime();
    return bTime - aTime;
  });
}

function sortMessagesByCreatedAt(messages: AssistantApiConversationMessage[]): AssistantApiConversationMessage[] {
  return [...messages].sort((a, b) => {
    const aTime = Number.isFinite(new Date(a.created_at).getTime()) ? new Date(a.created_at).getTime() : 0;
    const bTime = Number.isFinite(new Date(b.created_at).getTime()) ? new Date(b.created_at).getTime() : 0;
    return aTime - bTime;
  });
}

/** True when a synthesized tool return exists for this approval — i.e. the
 *  server recorded + resumed it, so the card is resolved even if the HTTP
 *  response the client saw failed. */
function approvalResultPresent(
  items: AssistantApiConversationMessage[] | null,
  approvalId: string,
): boolean {
  if (!items) return false;
  return items.some(
    (msg) => msg.kind === "TOOL_RETURN" && msg.tool_call_id === approvalId,
  );
}

/** A run that ended badly: its error is the truer thing to show than whatever
 *  text it had streamed before it went. */
function isConversationFailed(status: unknown): boolean {
  return typeof status === "string" && status.trim().toLowerCase() === "failed";
}

function isConversationRunning(status: unknown): boolean {
  if (typeof status !== "string") return false;
  const normalized = status.trim().toLowerCase();
  if (!normalized) return false;
  if (
    normalized === "waiting"
    || normalized === "completed"
    || normalized === "failed"
    || normalized === "cancelled"
    || normalized === "stopped"
  ) {
    return false;
  }
  return true;
}

function resolveScopedClient(client: LemmaClient, podId?: string | null): LemmaClient {
  if (podId && podId !== client.podId) {
    return client.withPod(podId);
  }
  return client;
}

function conversationUploadDirectory(conversationId: string): string {
  return `/me/conversations/${conversationId}`;
}

function shouldIgnoreFolderEnsureError(error: unknown): boolean {
  const message = error instanceof Error ? error.message.toLowerCase() : String(error ?? "").toLowerCase();
  return message.includes("already exists")
    || message.includes("already in use")
    || message.includes("path unavailable")
    || message.includes("path already")
    || message.includes("409");
}

async function ensureFolder(client: LemmaClient, name: string, directoryPath: string): Promise<void> {
  try {
    await client.files.folder.create(name, { directoryPath });
  } catch (error) {
    if (!shouldIgnoreFolderEnsureError(error)) throw error;
  }
}

async function ensureConversationUploadDirectory(client: LemmaClient, conversationId: string): Promise<string> {
  await ensureFolder(client, "conversations", "/me");
  await ensureFolder(client, conversationId, "/me/conversations");
  return conversationUploadDirectory(conversationId);
}

async function uploadConversationFiles(
  client: LemmaClient,
  conversationId: string,
  uploads: AssistantPendingFileUpload[],
  onStatus?: (key: string, next: Partial<AssistantPendingFileUpload>) => void,
): Promise<FileResponse[]> {
  const directoryPath = await ensureConversationUploadDirectory(client, conversationId);
  const uploaded: FileResponse[] = [];
  for (const upload of uploads) {
    onStatus?.(upload.key, { status: "uploading", error: undefined });
    try {
      const response = await client.files.upload(upload.file, {
        name: upload.file.name,
        directoryPath,
        searchEnabled: true,
      });
      onStatus?.(upload.key, { status: "uploaded", path: response.path, error: undefined });
      uploaded.push(response);
    } catch (error) {
      onStatus?.(upload.key, {
        status: "failed",
        error: error instanceof Error ? error.message : "Upload failed",
      });
      throw error;
    }
  }
  return uploaded;
}

function formatPersonalFileReferences(files: FileResponse[]): string {
  return files
    .map((file) => {
      const pathParts = file.path.split("/").filter(Boolean);
      const name = file.name || pathParts[pathParts.length - 1] || file.path;
      return `- ${name}: ${file.path}`;
    })
    .join("\n");
}

function appendPersonalFileReferences(content: string, files: FileResponse[]): string {
  if (files.length === 0) return content;
  const references = formatPersonalFileReferences(files);
  return `${content}\n\nPersonal files available to this run:\n${references}`;
}

export function useAssistantController({
  client,
  podId,
  agentName,
  assistantName,
  assistantId,
  organizationId,
  enabled = true,
  autoLoad = true,
  instructions,
  autoLoadMessages = true,
  historyScope = "pod",
}: UseAssistantControllerOptions): UseAssistantControllerResult {
  const [localError, setLocalError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<AvailableModelInfo[]>([]);
  const [conversationModel, setConversationModelState] = useState<ConversationModel | null>(null);
  const [conversationRuntime, setConversationRuntimeState] = useState<AgentRuntimeConfig | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [isLoadingMoreConversations, setIsLoadingMoreConversations] = useState(false);
  const [conversationsCursor, setConversationsCursor] = useState<string | null>(null);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [isLoadingOlderMessages, setIsLoadingOlderMessages] = useState(false);
  const [isUploadingFiles, setIsUploadingFiles] = useState(false);
  const [pendingFileUploads, setPendingFileUploads] = useState<AssistantPendingFileUpload[]>([]);
  const [olderMessagesCursor, setOlderMessagesCursor] = useState<string | null>(null);
  // Pagination position per conversation. A retained transcript is not reloaded
  // when you return to it, so without this its cursor would stay null and
  // "Load earlier activity" would vanish from a conversation that has more.
  const olderMessagesCursorsRef = useRef<Map<string, string | null>>(new Map());

  const activeConversationIdRef = useRef<string | null>(null);
  const conversationsRef = useRef<Conversation[]>([]);
  const heldStreamingThinkingRef = useRef<HeldStreamingThinking | null>(null);
  const heldStreamingTextRef = useRef<HeldStreamingText | null>(null);
  const isStreamingRef = useRef(false);
  const sessionIsStreamingRef = useRef(false);
  // Which conversations have had their history loaded in this session. A set,
  // not a single "last" id: the runtime store retains several transcripts, so
  // re-opening one that is still resident must not trigger a second load.
  const loadedConversationIdsRef = useRef<Set<string>>(new Set());
  const loadingConversationIdRef = useRef<string | null>(null);
  const skipInitialLoadConversationIdsRef = useRef<Set<string>>(new Set());
  // The detail fetch each open starts, kept by id so the transcript load that
  // races it can read the answer instead of asking for it again. Values never
  // reject — a failed fetch resolves to null, which reads as "we do not know".
  const conversationDetailsRef = useRef<Map<string, Promise<Conversation | null>>>(new Map());
  const loadConversationMessagesRef = useRef<((conversationId: string) => Promise<AssistantApiConversationMessage[] | null>) | null>(null);
  const resumeConversationIfRunningRef = useRef<((conversationId: string) => Promise<boolean>) | null>(null);
  // Which scope's conversation list and model catalog have already been
  // fetched. Both effects below are re-entered on every identity change of the
  // loader they call — and twice on mount under StrictMode — so without a key
  // to compare against, one mount is two of each request.
  const loadedHistoryScopeKeyRef = useRef<string | null>(null);
  const loadedModelsScopeKeyRef = useRef<string | null>(null);

  const scope = useMemo<AssistantConversationScope>(() => ({
    podId: podId ?? null,
    agentName: agentName ?? assistantName ?? assistantId ?? null,
    assistantName: assistantName ?? assistantId ?? null,
    assistantId: assistantId ?? null,
    organizationId: organizationId ?? null,
  }), [agentName, assistantId, assistantName, organizationId, podId]);
  const pendingFiles = useMemo(() => pendingFileUploads.map((upload) => upload.file), [pendingFileUploads]);

  const scopeKey = useMemo(
    () => JSON.stringify({
      podId: scope.podId ?? null,
      agentName: scope.agentName ?? null,
      assistantName: scope.assistantName ?? null,
      assistantId: scope.assistantId ?? null,
      organizationId: scope.organizationId ?? null,
    }),
    [scope.agentName, scope.assistantId, scope.assistantName, scope.organizationId, scope.podId],
  );
  const historyPodId = scope.podId ?? client.podId ?? null;
  // `undefined` means the agent is no part of the history request — and so no
  // part of anything derived from it. Under pod scope a surface that switches
  // agents must keep the list it already has rather than refetch the same one.
  // Under agent scope `null` is the pod's default agent, which is what a
  // controller with no agent name talks to.
  const historyAgentName = historyScope === "agent" ? scope.agentName ?? null : undefined;
  const historyScopeKey = useMemo(
    () => JSON.stringify(
      typeof historyAgentName === "undefined"
        ? { podId: historyPodId }
        : { podId: historyPodId, agentName: historyAgentName },
    ),
    [historyAgentName, historyPodId],
  );
  const previousHistoryScopeKeyRef = useRef(historyScopeKey);
  const previousScopeKeyRef = useRef(scopeKey);
  // The catalog is per-organization and nothing else, so this is the whole of
  // what would make a second fetch return something different.
  const modelsScopeKey = scope.organizationId ?? "";

  // A failed message load comes back from the session as an empty page, which
  // reads exactly like a conversation that has nothing in it. Counting the
  // errors it reports on the way is how a load can tell the two apart.
  const sessionErrorCountRef = useRef(0);

  const handleAssistantSessionError = useCallback((sessionError: unknown) => {
    sessionErrorCountRef.current += 1;
    setLocalError((prev) => prev || (sessionError instanceof Error ? sessionError.message : "Agent session failed"));
  }, []);

  const assistantSession = useAssistantSession({
    client,
    podId: scope.podId ?? undefined,
    agentName: scope.agentName ?? undefined,
    assistantName: scope.assistantName ?? undefined,
    assistantId: scope.assistantId ?? undefined,
    organizationId: scope.organizationId ?? undefined,
    instructions,
    conversationId: activeConversationId ?? undefined,
    autoLoad: false,
    onError: handleAssistantSessionError,
  });

  const {
    conversation: sessionConversation,
    conversationId: sessionConversationId,
    loadMessages: sessionLoadMessages,
    sendMessage: sessionSendMessage,
    retryFailedRun: sessionRetryFailedRun,
    createConversation: sessionCreateConversation,
    resumeIfRunning: sessionResumeIfRunning,
    stop: sessionStop,
    cancel: sessionCancel,
    isStreaming: sessionIsStreaming,
    messages: sessionMessages,
    streamingText: sessionStreamingText,
    streamingThinking: sessionStreamingThinking,
    streamingTool: sessionStreamingTool,
    status: sessionStatus,
  } = assistantSession;

  const {
    runtimeMessages,
    appendOptimisticUserMessage,
    replaceLoadedMessages,
    mergeMessages,
    adoptPendingMessages,
    dropPendingMessages,
    clear: clearRuntimeMessages,
  } = useAssistantRuntime({
    conversationId: activeConversationId,
    sessionConversationId,
    sessionMessages,
    // The store is what actually holds transcripts, so it decides what counts
    // as loaded. Without this the set below kept saying yes about conversations
    // retention had already thrown away, and the open skipped its own fetch.
    onConversationsDropped: (droppedConversationIds) => {
      droppedConversationIds.forEach((droppedConversationId) => {
        loadedConversationIdsRef.current.delete(droppedConversationId);
        olderMessagesCursorsRef.current.delete(droppedConversationId);
      });
    },
  });

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeConversationId),
    [activeConversationId, conversations],
  );
  const persistedRunError = (
    activeConversation?.last_run_status?.toUpperCase() === "FAILED"
    && typeof activeConversation.last_run_error === "string"
  )
    ? activeConversation.last_run_error
    : null;
  const error = localError ?? persistedRunError;
  const canRetryFailedMessage = (
    activeConversation?.last_run_status?.toUpperCase() === "FAILED"
    && activeConversation.last_run_retryable === true
  );
  const isLoading = isStreaming || sessionIsStreaming;

  const touchConversation = useCallback((conversationId: string, updates?: Partial<Conversation>) => {
    setConversations((prev) => {
      const now = new Date().toISOString();
      let found = false;
      const next = prev.map((conversation) => {
        if (conversation.id !== conversationId) return conversation;
        found = true;
        return {
          ...conversation,
          ...updates,
          updated_at: typeof updates?.updated_at === "undefined"
            ? conversation.updated_at
            : updates.updated_at || now,
        };
      });
      return found ? sortConversationsByUpdatedAt(next) : next;
    });
  }, []);

  const refreshConversationDetail = useCallback(async (
    conversationId: string,
  ): Promise<Conversation> => {
    const knownConversation = conversationsRef.current.find(
      (conversation) => conversation.id === conversationId,
    );
    const request = client.conversations.get(conversationId, {
      pod_id: knownConversation?.pod_id ?? scope.podId ?? undefined,
    });
    conversationDetailsRef.current.set(conversationId, request.catch(() => null));
    const detail = await request;
    setConversations((previous) => sortConversationsByUpdatedAt([
      detail,
      ...previous.filter((conversation) => conversation.id !== detail.id),
    ]));
    return detail;
  }, [client, scope.podId]);

  // Resuming is only ever about a conversation that is still running, and the
  // open path has just fetched the record that says whether it is. Waiting on
  // that fetch costs nothing it was not already waiting on, and hands the
  // session the answer instead of leaving it to fetch the same record again.
  const resumeConversationIfRunning = useCallback(async (conversationId: string): Promise<boolean> => {
    const knownConversation = await conversationDetailsRef.current.get(conversationId);
    return (await sessionResumeIfRunning(conversationId, { knownConversation })) ?? false;
  }, [sessionResumeIfRunning]);

  const setConversationModel = useCallback(async (model: ConversationModel | null, runtime?: AgentRuntimeConfig | null) => {
    const nextRuntime = typeof runtime === "undefined"
      ? availableModels.find((entry) => entry.id === model)?.runtime ?? null
      : runtime;
    setConversationModelState(model);
    setConversationRuntimeState(nextRuntime);

    const conversationId = activeConversationIdRef.current;
    if (!conversationId) return;

    const knownConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
    const resolvedPodId = knownConversation?.pod_id ?? scope.podId;
    const previousModel = knownConversation?.model ?? null;
    const previousRuntime = knownConversation?.agent_runtime ?? null;

    touchConversation(conversationId, {
      model: model as Conversation["model"],
      agent_runtime: nextRuntime,
    });
    try {
      const updatedConversation = await client.conversations.update(
        conversationId,
        model
          ? { model: model as never, agent_runtime: nextRuntime }
          : { agent_runtime: null },
        { pod_id: resolvedPodId ?? undefined },
      );
      touchConversation(conversationId, {
        model: (updatedConversation.model ?? model) as Conversation["model"],
        agent_runtime: updatedConversation.agent_runtime ?? nextRuntime,
        updated_at: updatedConversation.updated_at,
      });
      setConversationModelState((updatedConversation.model ?? model) as ConversationModel | null);
      setConversationRuntimeState(updatedConversation.agent_runtime ?? nextRuntime);
    } catch (error) {
      touchConversation(conversationId, {
        model: previousModel,
        agent_runtime: previousRuntime,
      });
      setConversationModelState(previousModel);
      setConversationRuntimeState(previousRuntime);
      throw error;
    }
  }, [availableModels, client, scope.podId, touchConversation]);

  const listConversationHistory = useCallback(async (input: {
    limit?: number;
    pageToken?: string;
  } = {}) => {
    const scopedClient = scope.podId && scope.podId !== client.podId
      ? client.withPod(scope.podId)
      : client;
    return scopedClient.conversations.list({
      pod_id: scope.podId ?? scopedClient.podId ?? undefined,
      // Left off the request under pod scope: the parameter is what narrows the
      // list, and `null` already means the pod's default agent, not "no filter".
      ...(typeof historyAgentName === "undefined" ? {} : { agent_name: historyAgentName }),
      limit: input.limit,
      page_token: input.pageToken,
    });
  }, [client, historyAgentName, scope.podId]);

  // Reports whether the list actually landed, so the caller's "already loaded
  // this scope" key can be released after a failure instead of caching it.
  const loadConversations = useCallback(async (): Promise<boolean> => {
    setIsLoadingConversations(true);
    try {
      const response = await listConversationHistory({ limit: CONVERSATIONS_PAGE_SIZE });
      const nextConversations = sortConversationsByUpdatedAt(response.items || []);
      setConversations((currentConversations) => {
        const openedConversationId = activeConversationIdRef.current;
        if (!openedConversationId || nextConversations.some((conversation) => conversation.id === openedConversationId)) {
          return nextConversations;
        }
        const openedConversation = currentConversations.find((conversation) => conversation.id === openedConversationId);
        return openedConversation
          ? sortConversationsByUpdatedAt([...nextConversations, openedConversation])
          : nextConversations;
      });
      setConversationsCursor(response.next_page_token ?? null);
      return true;
    } catch (err) {
      setLocalError((prev) => prev || (err instanceof Error ? err.message : "Failed to load conversations"));
      return false;
    } finally {
      setIsLoadingConversations(false);
    }
  }, [listConversationHistory]);

  const loadMoreConversations = useCallback(async (): Promise<Conversation[]> => {
    if (!conversationsCursor || isLoadingConversations || isLoadingMoreConversations) {
      return [];
    }

    setIsLoadingMoreConversations(true);
    try {
      const response = await listConversationHistory({
        limit: CONVERSATIONS_PAGE_SIZE,
        pageToken: conversationsCursor,
      });
      const moreConversations = response.items || [];
      setConversations((prev) => {
        const byId = new Map(prev.map((conversation) => [conversation.id, conversation]));
        for (const conversation of moreConversations) {
          byId.set(conversation.id, conversation);
        }
        return sortConversationsByUpdatedAt(Array.from(byId.values()));
      });
      setConversationsCursor(response.next_page_token ?? null);
      return moreConversations;
    } catch (err) {
      setLocalError((prev) => prev || (err instanceof Error ? err.message : "Failed to load more conversations"));
      return [];
    } finally {
      setIsLoadingMoreConversations(false);
    }
  }, [conversationsCursor, isLoadingConversations, isLoadingMoreConversations, listConversationHistory]);

  // Throws rather than flattening a failure to `[]`: an empty catalog and a
  // catalog we could not reach look identical to the caller otherwise, and the
  // caller now has to tell them apart to know whether asking again is worth it.
  const loadAvailableModels = useCallback(async (): Promise<AvailableModelInfo[]> => {
    const response = await client.conversations.listModels({
      orgId: scope.organizationId ?? undefined,
    });
    return response.items ?? [];
  }, [client, scope.organizationId]);

  const loadConversationMessages = useCallback(async (
    conversationId: string,
  ): Promise<AssistantApiConversationMessage[] | null> => {
    setIsLoadingMessages(true);
    const errorsBeforeLoad = sessionErrorCountRef.current;
    try {
      const response = await sessionLoadMessages({
        conversationId,
        limit: 100,
      });
      if (activeConversationIdRef.current !== conversationId) {
        return null;
      }
      // Null means "no transcript to hold", so the caller re-fetches next time
      // rather than caching the empty page a failed request handed back.
      if (sessionErrorCountRef.current !== errorsBeforeLoad) {
        return null;
      }
      const sorted = sortMessagesByCreatedAt((response.items || []) as AssistantApiConversationMessage[]);
      replaceLoadedMessages(sorted);
      setOlderMessagesCursor(response.next_page_token ?? null);
      return sorted;
    } catch (err) {
      setLocalError((prev) => prev || (err instanceof Error ? err.message : "Failed to load messages"));
      setOlderMessagesCursor(null);
      return null;
    } finally {
      setIsLoadingMessages(false);
    }
  }, [replaceLoadedMessages, sessionLoadMessages]);

  const loadOlderMessages = useCallback(async (): Promise<boolean> => {
    const conversationId = activeConversationIdRef.current;
    const cursor = olderMessagesCursor;

    if (!conversationId || !cursor || isLoadingMessages || isLoadingOlderMessages) {
      return false;
    }

    setIsLoadingOlderMessages(true);
    try {
      const response = await sessionLoadMessages({
        conversationId,
        limit: 100,
        pageToken: cursor,
      });

      if (activeConversationIdRef.current !== conversationId) {
        return false;
      }

      const older = sortMessagesByCreatedAt((response.items || []) as AssistantApiConversationMessage[]);
      mergeMessages(older);
      setOlderMessagesCursor(response.next_page_token ?? null);
      return older.length > 0;
    } catch (err) {
      setLocalError((prev) => prev || (err instanceof Error ? err.message : "Failed to load older messages"));
      return false;
    } finally {
      setIsLoadingOlderMessages(false);
    }
  }, [isLoadingMessages, isLoadingOlderMessages, mergeMessages, olderMessagesCursor, sessionLoadMessages]);

  useEffect(() => {
    loadConversationMessagesRef.current = loadConversationMessages;
  }, [loadConversationMessages]);

  useEffect(() => {
    resumeConversationIfRunningRef.current = resumeConversationIfRunning;
  }, [resumeConversationIfRunning]);

  useEffect(() => {
    activeConversationIdRef.current = activeConversationId;
  }, [activeConversationId]);

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    isStreamingRef.current = isStreaming;
  }, [isStreaming]);

  useEffect(() => {
    sessionIsStreamingRef.current = sessionIsStreaming;
  }, [sessionIsStreaming]);

  useEffect(() => {
    if (!enabled) {
      loadedModelsScopeKeyRef.current = null;
      setAvailableModels([]);
      return;
    }
    if (!autoLoad) return;
    // Keyed rather than bare, for the same reason the transcript load is: this
    // effect is re-entered whenever `loadAvailableModels` changes identity, and
    // under StrictMode it is entered twice on mount. The catalog is a function
    // of the org alone, so asking again for the same org is asking twice.
    if (loadedModelsScopeKeyRef.current === modelsScopeKey) return;
    loadedModelsScopeKeyRef.current = modelsScopeKey;

    let cancelled = false;
    void loadAvailableModels()
      .then((models) => {
        if (cancelled) return;
        setAvailableModels(models);
      })
      .catch(() => {
        // Nothing was loaded, so nothing is cached: let the next run retry.
        if (loadedModelsScopeKeyRef.current === modelsScopeKey) {
          loadedModelsScopeKeyRef.current = null;
        }
      });

    return () => {
      cancelled = true;
    };
  }, [autoLoad, enabled, loadAvailableModels, modelsScopeKey]);

  const messages = useMemo(() => {
    // A message with no conversation of its own is the turn you just sent, put
    // on screen before the conversation it belongs to exists. Filtering it out
    // is what made the first message of a new conversation vanish for the
    // length of the create round-trip; `adoptPendingMessages` stamps it with
    // the real id the moment there is one, and a failed send drops it.
    const normalized = sortMessagesByCreatedAt(runtimeMessages as AssistantApiConversationMessage[])
      .filter((message) => (
        !message.conversation_id
        || (!!activeConversationId && message.conversation_id === activeConversationId)
      ));
    if (!activeConversationId && normalized.length === 0) return [];
    if (
      normalized.length === 0
      && sessionStreamingText.trim().length === 0
      && sessionStreamingThinking.trim().length === 0
      && heldStreamingThinkingRef.current === null
      && heldStreamingTextRef.current === null
    ) return [];

    const nextMessages = mapConversationMessages(normalized);
    // Streamed thinking and text belong to a run, and a run belongs to a
    // conversation — so with none open there is nothing streaming to append.
    if (!activeConversationId) return nextMessages;

    const pendingThinking = resolveStreamingThinking({
      held: heldStreamingThinkingRef,
      conversationId: activeConversationId,
      streamed: sessionStreamingThinking.trim(),
      messages: normalized,
      isRunning: isConversationRunning(sessionStatus),
    });
    if (pendingThinking.length > 0) {
      const streamingId = `streaming-thinking-${activeConversationId}`;
      nextMessages.push({
        id: streamingId,
        role: "assistant",
        content: "",
        createdAt: new Date(),
        parts: [{
          id: `${streamingId}-reasoning`,
          type: "reasoning",
          text: pendingThinking,
          state: "streaming",
        }],
        kind: "THINKING",
      });
    }
    const pendingText = resolveStreamingText({
      held: heldStreamingTextRef,
      conversationId: activeConversationId,
      streamed: sessionStreamingText.trim(),
      messages: normalized,
      failed: isConversationFailed(sessionStatus),
    });
    if (pendingText.length > 0) {
      const streamingId = `streaming-${activeConversationId}`;
      nextMessages.push({
        id: streamingId,
        role: "assistant",
        content: pendingText,
        createdAt: new Date(),
        parts: [{ id: `${streamingId}-text`, type: "text", text: pendingText }],
      });
    }

    return nextMessages;
  }, [activeConversationId, runtimeMessages, sessionStatus, sessionStreamingText, sessionStreamingThinking]);

  useEffect(() => {
    if (!sessionConversation || sessionConversation.id !== activeConversationId) return;
    setConversations((previous) => sortConversationsByUpdatedAt([
      sessionConversation,
      ...previous.filter((conversation) => conversation.id !== sessionConversation.id),
    ]));
  }, [activeConversationId, sessionConversation]);

  // `sessionStatus` describes whichever conversation the session is attached
  // to, and on the render that switches conversations that is still the one we
  // just left: the session resets its own status from an effect registered
  // earlier in this component, so the reset is only queued, not applied. Writing
  // it unguarded stamps the conversation you left onto the one you opened —
  // and, because the reset arrives as `undefined`, the guard below would then
  // skip the correction and leave the wrong status in place. Only write when
  // the session is actually reporting on the conversation being written to.
  useEffect(() => {
    if (!activeConversationId) return;
    if (sessionConversationId !== activeConversationId) return;
    if (!sessionStatus) return;

    touchConversation(activeConversationId, {
      status: sessionStatus as Conversation["status"],
      ...(isConversationRunning(sessionStatus)
        ? {
            last_run_status: "RUNNING" as Conversation["last_run_status"],
            last_run_error: null,
            last_run_retryable: false,
          }
        : {}),
    });
  }, [activeConversationId, sessionConversationId, sessionStatus, touchConversation]);

  useEffect(() => {
    if (!activeConversationId) return;
    olderMessagesCursorsRef.current.set(activeConversationId, olderMessagesCursor);
  }, [activeConversationId, olderMessagesCursor]);

  useEffect(() => {
    if (!activeConversationId) return;
    const activeConversation = conversations.find((conversation) => conversation.id === activeConversationId);
    if (!activeConversation) return;
    setConversationModelState(activeConversation.model ?? null);
    setConversationRuntimeState(activeConversation.agent_runtime ?? null);
  }, [activeConversationId, conversations]);

  useEffect(() => {
    const historyScopeChanged = previousHistoryScopeKeyRef.current !== historyScopeKey;
    const scopeChanged = previousScopeKeyRef.current !== scopeKey;
    previousHistoryScopeKeyRef.current = historyScopeKey;
    previousScopeKeyRef.current = scopeKey;

    if (!enabled) {
      sessionCancel();
      clearRuntimeMessages();
      activeConversationIdRef.current = null;
      loadedConversationIdsRef.current.clear();
      olderMessagesCursorsRef.current.clear();
      loadingConversationIdRef.current = null;
      skipInitialLoadConversationIdsRef.current.clear();
      conversationDetailsRef.current.clear();
      setActiveConversationId(null);
      setAvailableModels([]);
      setConversationModelState(null);
      setConversationRuntimeState(null);
      setConversations([]);
      setConversationsCursor(null);
      setLocalError(null);
      setOlderMessagesCursor(null);
      setIsLoadingConversations(false);
      setIsLoadingMoreConversations(false);
      setIsLoadingMessages(false);
      setIsLoadingOlderMessages(false);
      return;
    }

    // Nothing to leave on the first run, so nothing to clear. Resetting
    // unconditionally made mounting destructive: a consumer that opens a
    // conversation from its own mount effect runs *before* this one (child
    // effects precede the parent's), so this landed afterwards and closed the
    // conversation it had just opened — a transcript that stayed blank until
    // something else happened to re-open it.
    if (!scopeChanged && !historyScopeChanged) return;

    activeConversationIdRef.current = null;
    loadedConversationIdsRef.current.clear();
    olderMessagesCursorsRef.current.clear();
    loadingConversationIdRef.current = null;
    skipInitialLoadConversationIdsRef.current.clear();
    conversationDetailsRef.current.clear();
    setActiveConversationId(null);
    setConversationModelState(null);
    setConversationRuntimeState(null);
    if (historyScopeChanged) {
      setConversations([]);
      setConversationsCursor(null);
    }
    setLocalError(null);
    clearRuntimeMessages();
    setOlderMessagesCursor(null);
  }, [clearRuntimeMessages, enabled, historyScopeKey, scopeKey, sessionCancel]);

  useEffect(() => {
    // No pod, nothing to list — the request would only fail on the missing id.
    if (!enabled || !autoLoad || !historyPodId) {
      loadedHistoryScopeKeyRef.current = null;
      return;
    }
    // The list is a function of the scope, and this effect re-runs on every
    // identity change of `loadConversations` — plus twice on mount under
    // StrictMode. One scope, one list request. The scope-reset effect above
    // clears this key when the scope actually changes.
    if (loadedHistoryScopeKeyRef.current === historyScopeKey) return;
    loadedHistoryScopeKeyRef.current = historyScopeKey;
    void loadConversations().then((loaded) => {
      // Nothing was listed, so nothing is cached: let the next run try again.
      if (!loaded && loadedHistoryScopeKeyRef.current === historyScopeKey) {
        loadedHistoryScopeKeyRef.current = null;
      }
    });
  }, [autoLoad, enabled, historyPodId, historyScopeKey, loadConversations]);

  useEffect(() => {
    // Having no conversation open is not a reason to forget the ones already
    // loaded — `messages` is filtered by the active id, so a retained transcript
    // is invisible until it is asked for again. Only leaving the scope entirely
    // (handled by the scope effect above) drops the store.
    if (!enabled || !activeConversationId) {
      loadingConversationIdRef.current = null;
      setOlderMessagesCursor(null);
      setIsLoadingMessages(false);
      return;
    }

    // Deferred loading, not discarded loading. This branch used to clear the
    // store, so a gate that dipped false for one render made the side view
    // re-fetch and re-paint a transcript it already had.
    if (!autoLoadMessages) {
      loadingConversationIdRef.current = null;
      setOlderMessagesCursor(null);
      setIsLoadingMessages(false);
      return;
    }

    // Every branch that decides not to fetch has to put the loading flag down
    // on its way out. `selectConversation` raises it optimistically, and a
    // return that leaves it up is a spinner nothing will ever come back to.
    if (skipInitialLoadConversationIdsRef.current.has(activeConversationId)) {
      skipInitialLoadConversationIdsRef.current.delete(activeConversationId);
      loadedConversationIdsRef.current.add(activeConversationId);
      setIsLoadingMessages(false);
      return;
    }

    if (loadedConversationIdsRef.current.has(activeConversationId)) {
      setIsLoadingMessages(false);
      return;
    }
    if (loadingConversationIdRef.current === activeConversationId) {
      return;
    }

    let cancelled = false;
    loadingConversationIdRef.current = activeConversationId;
    const loadConversation = async () => {
      setOlderMessagesCursor(null);
      const loaded = await loadConversationMessagesRef.current?.(activeConversationId);
      if (cancelled) return;
      // A load that failed left the store empty, so calling it loaded would
      // hold the transcript blank until the whole scope resets. Leaving it
      // unmarked costs one more request and gets the messages back.
      if (loaded) {
        loadedConversationIdsRef.current.add(activeConversationId);
      }
      try {
        await resumeConversationIfRunningRef.current?.(activeConversationId);
      } catch (error) {
        if (cancelled) return;
        setLocalError((prev) => prev || (error instanceof Error ? error.message : "Failed to resume conversation"));
      }
    };

    void loadConversation().finally(() => {
      if (loadingConversationIdRef.current === activeConversationId) {
        loadingConversationIdRef.current = null;
      }
    });
    return () => {
      cancelled = true;
    };
  }, [activeConversationId, autoLoadMessages, clearRuntimeMessages, enabled]);

  const stop = useCallback(() => {
    const hadActiveStream = sessionIsStreamingRef.current || isStreamingRef.current;
    sessionCancel();
    setIsStreaming(false);
    const conversationId = activeConversationIdRef.current;
    if (!conversationId) return;
    const activeConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
    const conversationIsRunning = isConversationRunning(activeConversation?.status);
    if (!hadActiveStream && !conversationIsRunning) return;
    const previousStatus = activeConversation?.status;
    // The conversation is winding down, not waiting on the person who just
    // asked it to stop. `WAITING` is the backend's word for "needs your input",
    // so borrowing it here made Stop ask for a reply it does not want.
    touchConversation(conversationId, { status: "stop_requested" as Conversation["status"] });
    void sessionStop(conversationId).catch((error) => {
      touchConversation(conversationId, { status: previousStatus });
      setLocalError((prev) => prev || (error instanceof Error ? error.message : "Failed to stop conversation"));
    });
  }, [sessionCancel, sessionStop, touchConversation]);

  const selectConversation = useCallback((conversationId: string | null) => {
    const currentConversationId = activeConversationIdRef.current;
    const isSwitchingAway = currentConversationId !== conversationId;
    const wasStreaming = sessionIsStreamingRef.current || isStreamingRef.current;
    // Re-selecting the conversation already open is not leaving it. Cancelling
    // unconditionally meant clicking the open conversation in the history list
    // killed the stream it was in the middle of, and every path below then
    // treated the transcript as one it already holds — so nothing reattached.
    if (wasStreaming && isSwitchingAway) {
      sessionCancel();
      setIsStreaming(false);
    }

    // The turn we are walking out on keeps going without us. Whatever it writes
    // from here — the rest of the answer, its tool calls, the durable messages
    // the aborted stream will never deliver — lands in the database and nowhere
    // near the store, so the transcript we are holding stops being the current
    // one the moment we look away.
    //
    // Saying so is the whole fix: `loadedConversationIds` is the claim "we hold
    // this transcript and it is up to date", and the two paths that open a
    // conversation both read it and skip *both* the re-list and the resume when
    // it says yes. Dropping the claim sends the re-open back through the full
    // path, which reloads what landed while we were away and reattaches to the
    // run if it is still going. Retention still holds the messages, so the
    // re-open paints them immediately and the catch-up fills in behind it.
    if (currentConversationId && isSwitchingAway) {
      const leftBehind = conversationsRef.current.find(
        (conversation) => conversation.id === currentConversationId,
      );
      if (wasStreaming || isConversationRunning(leftBehind?.status)) {
        loadedConversationIdsRef.current.delete(currentConversationId);
      }
    }

    if (conversationId) {
      void refreshConversationDetail(conversationId)
        .then((openedConversation) => {
          if (activeConversationIdRef.current !== conversationId) return;
          setConversationModelState(openedConversation.model ?? null);
          setConversationRuntimeState(openedConversation.agent_runtime ?? null);
        })
        .catch((detailError) => {
          if (activeConversationIdRef.current !== conversationId) return;
          setLocalError((previous) => previous || (
            detailError instanceof Error
              ? detailError.message
              : "Failed to load conversation"
          ));
        });
    }
    if (conversationId && conversationId === currentConversationId) {
      if (!autoLoadMessages) {
        setLocalError(null);
        setOlderMessagesCursor(null);
        setIsLoadingMessages(false);
        return;
      }

      if (
        loadingConversationIdRef.current === conversationId
        || loadedConversationIdsRef.current.has(conversationId)
      ) {
        return;
      }

      loadingConversationIdRef.current = conversationId;
      setLocalError(null);
      setOlderMessagesCursor(null);
      setIsLoadingMessages(true);
      void loadConversationMessagesRef.current?.(conversationId)
        .then((loaded) => {
          if (loaded) {
            loadedConversationIdsRef.current.add(conversationId);
          }
          return resumeConversationIfRunningRef.current?.(conversationId);
        })
        .catch((error) => {
          setLocalError((prev) => prev || (error instanceof Error ? error.message : "Failed to resume conversation"));
        })
        .finally(() => {
          if (loadingConversationIdRef.current === conversationId) {
            loadingConversationIdRef.current = null;
          }
        });
      return;
    }

    setLocalError(null);
    // Leaving mid-send abandons the turn that was still waiting for its
    // conversation; it must not follow you to the one you just opened.
    dropPendingMessages();
    activeConversationIdRef.current = conversationId;
    loadingConversationIdRef.current = null;
    // The store keeps the last few transcripts, so switching to one that is
    // still resident is a swap, not a load: no wipe, and no loading state to
    // paint a skeleton over messages we are holding. Asked of the loaded set
    // rather than of the messages, so a conversation that is genuinely empty
    // reads as held instead of being re-fetched on every click.
    const isResident = Boolean(conversationId && loadedConversationIdsRef.current.has(conversationId));
    setOlderMessagesCursor(
      isResident && conversationId
        ? olderMessagesCursorsRef.current.get(conversationId) ?? null
        : null,
    );
    setIsLoadingMessages(Boolean(conversationId && autoLoadMessages && !isResident));
    setActiveConversationId(conversationId);
  }, [autoLoadMessages, dropPendingMessages, refreshConversationDetail, sessionCancel]);

  const openConversation = useCallback((conversationId: string) => {
    selectConversation(conversationId);
  }, [selectConversation]);

  const closeConversation = useCallback(() => {
    selectConversation(null);
  }, [selectConversation]);

  const resetConversationState = useCallback((keepPendingFiles = false) => {
    stop();
    clearRuntimeMessages();
    activeConversationIdRef.current = null;
    loadedConversationIdsRef.current.clear();
    olderMessagesCursorsRef.current.clear();
    loadingConversationIdRef.current = null;
    skipInitialLoadConversationIdsRef.current.clear();
    conversationDetailsRef.current.clear();
    setActiveConversationId(null);
    setLocalError(null);
    setOlderMessagesCursor(null);
    setIsLoadingMessages(false);
    if (!keepPendingFiles) {
      setPendingFileUploads([]);
    }
  }, [clearRuntimeMessages, stop]);

  const clearMessages = useCallback(() => {
    resetConversationState(false);
  }, [resetConversationState]);

  const ensureConversation = useCallback(async (
    titleSeed: string,
    options: { instructions?: string | null; metadata?: Record<string, unknown> | null } = {},
  ): Promise<string> => {
    const existingConversationId = activeConversationIdRef.current;
    if (existingConversationId) {
      return existingConversationId;
    }

    const createdConversation = await sessionCreateConversation({
      // No title: the server always starts one with none, so real title
      // generation runs unconditionally rather than depending on this caller
      // (or any other) leaving it out. The sidebar shows titleSeed below
      // instead -- a local display value the server never sees.
      instructions: typeof options.instructions === "undefined" ? instructions : options.instructions,
      metadata: options.metadata ?? undefined,
      model: conversationModel as unknown as never,
      agentRuntime: conversationRuntime,
      ...scope,
    });

    // A display-only stand-in for the sidebar until the real title lands
    // (via the live conversation-updated event or the next refetch) --
    // never sent to or persisted by the server.
    const displayConversation: Conversation = {
      ...createdConversation,
      title: createdConversation.title ?? titleSeed.slice(0, 120),
    };

    const nextConversations = sortConversationsByUpdatedAt([
      displayConversation,
      ...conversationsRef.current.filter((conversation) => conversation.id !== createdConversation.id),
    ]);
    // Written to the ref as well as the state, because the send that follows
    // reads the record from here in the same tick — before the effect that
    // mirrors state into this ref has had a render to run in.
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    activeConversationIdRef.current = createdConversation.id;
    loadedConversationIdsRef.current.add(createdConversation.id);
    loadingConversationIdRef.current = null;
    skipInitialLoadConversationIdsRef.current.add(createdConversation.id);
    setActiveConversationId(createdConversation.id);
    setConversationModelState((createdConversation.model ?? conversationModel ?? null) as ConversationModel | null);
    setConversationRuntimeState(createdConversation.agent_runtime ?? conversationRuntime ?? null);
    // Keeps the turn that triggered this create — it is on screen already and
    // is about to be sent into the conversation being made for it.
    clearRuntimeMessages({ keepPending: true });
    setOlderMessagesCursor(null);

    return createdConversation.id;
  }, [clearRuntimeMessages, conversationModel, conversationRuntime, instructions, scope, sessionCreateConversation]);

  const queuePendingFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;
    setPendingFileUploads((prev) => {
      const byKey = new Map<string, AssistantPendingFileUpload>();
      prev.forEach((upload) => byKey.set(upload.key, upload));
      files.forEach((file) => {
        const key = getFileKey(file);
        byKey.set(key, {
          key,
          file,
          status: "queued",
        });
      });
      return Array.from(byKey.values());
    });
  }, []);

  const removePendingFile = useCallback((fileKey: string) => {
    setPendingFileUploads((prev) => prev.filter((upload) => upload.key !== fileKey));
  }, []);

  const clearPendingFiles = useCallback(() => {
    setPendingFileUploads([]);
  }, []);

  const updatePendingFileUpload = useCallback((key: string, next: Partial<AssistantPendingFileUpload>) => {
    setPendingFileUploads((prev) => prev.map((upload) => (
      upload.key === key
        ? { ...upload, ...next }
        : upload
    )));
  }, []);

  const sendMessage = useCallback(async (content: string, options: SendAssistantControllerMessageOptions = {}) => {
    const trimmed = content.trim();
    const uploadsToSend = pendingFileUploads.filter((upload) => upload.status !== "uploaded");
    if (!enabled || (!trimmed && uploadsToSend.length === 0) || isStreaming || sessionIsStreaming) return;
    const forceNewConversation = options.forceNewConversation === true;

    setLocalError(null);
    if (forceNewConversation) {
      resetConversationState(true);
    }

    let conversationId = forceNewConversation ? null : activeConversationId;
    // A new turn is where the held answer from the last one stops being worth
    // holding: whatever it was waiting for either arrived, or is not coming.
    heldStreamingTextRef.current = null;
    // Raised before the create, not after it. This is what the transcript reads
    // to know it is no longer an empty conversation, so leaving it down for the
    // length of the round-trip left the empty state and its centred composer on
    // screen — and then snapped the whole column to the floor when the first
    // message landed.
    setIsStreaming(true);
    // Likewise the turn itself: with no attachments the text is already final,
    // so it can go up now rather than a round-trip later. An upload changes the
    // content (it appends the file references), so those still wait for it.
    const hasEagerOptimisticTurn = uploadsToSend.length === 0;
    if (hasEagerOptimisticTurn) {
      appendOptimisticUserMessage(trimmed, { conversationId });
    }
    try {
      if (!conversationId) {
        conversationId = await ensureConversation(trimmed, {
          instructions: options.instructions,
          metadata: options.conversationMetadata,
        });
        // The turn above went up without a conversation to belong to. It has
        // one now.
        if (conversationId) adoptPendingMessages(conversationId);
      }
      if (!conversationId) {
        throw new Error("Conversation could not be initialized");
      }
      const finalConversationId = conversationId;

      let messageContent = trimmed || "Please use the attached files.";
      let uploadedFiles: FileResponse[] = [];
      if (uploadsToSend.length > 0) {
        setIsUploadingFiles(true);
        try {
          const fileClient = resolveScopedClient(client, scope.podId);
          uploadedFiles = await uploadConversationFiles(fileClient, finalConversationId, uploadsToSend, updatePendingFileUpload);
          messageContent = appendPersonalFileReferences(messageContent, uploadedFiles);
          setPendingFileUploads([]);
          touchConversation(finalConversationId, { updated_at: new Date().toISOString() });
        } finally {
          setIsUploadingFiles(false);
        }
      }

      if (!hasEagerOptimisticTurn) {
        appendOptimisticUserMessage(messageContent, {
          conversationId: finalConversationId,
        });
      }

      touchConversation(finalConversationId, {
        status: "running" as Conversation["status"],
        last_run_status: "RUNNING" as Conversation["last_run_status"],
        last_run_error: null,
        last_run_retryable: false,
      });
      await sessionSendMessage(messageContent, {
        conversationId: finalConversationId,
        // The controller opened (or just created) this conversation and is
        // still holding the record; handing it over is what stops the session
        // fetching the same conversation again before every first send.
        knownConversation: conversationsRef.current.find(
          (conversation) => conversation.id === finalConversationId,
        ) ?? null,
        metadata: uploadedFiles.length > 0
          ? {
              ...(options.metadata ?? {}),
              attachments: uploadedFiles.map((file) => ({
                id: file.id,
                name: file.name,
                path: file.path,
                namespace: "PERSONAL",
                mime_type: file.mime_type,
              })),
            }
          : options.metadata ?? undefined,
      });
      touchConversation(finalConversationId, { updated_at: new Date().toISOString() });
    } catch (err) {
      // The conversation was never created, so the turn shown against it has
      // nothing to belong to. Left in the store it would surface in whichever
      // conversation is opened next, which is worse than losing it.
      if (!conversationId) dropPendingMessages();
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      if (conversationId) {
        await refreshConversationDetail(conversationId).catch(() => undefined);
      }
      setLocalError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setIsStreaming(false);
    }
  }, [
    activeConversationId,
    adoptPendingMessages,
    appendOptimisticUserMessage,
    dropPendingMessages,
    enabled,
    ensureConversation,
    isStreaming,
    pendingFileUploads,
    refreshConversationDetail,
    resetConversationState,
    scope.podId,
    sessionIsStreaming,
    sessionSendMessage,
    touchConversation,
    updatePendingFileUpload,
  ]);

  // Sibling to `sendMessage` for the "a run is already active" case. It
  // deliberately does not touch `isStreaming`/`sessionIsStreaming` or call
  // `consume()`: calling `sendMessage` again mid-stream would open a second
  // SSE subscription for the same run (genuine event duplication) and race
  // `sendMessage`'s own shared abort ref. The backend endpoint this calls
  // persists the message immediately either way -- joining the active run if
  // there is one -- so no second stream is needed here.
  const steerMessage = useCallback(async (
    content: string,
    options: SendAssistantControllerMessageOptions = {},
  ) => {
    const trimmed = content.trim();
    const conversationId = activeConversationIdRef.current;
    if (!enabled || !trimmed || !conversationId) return;

    setLocalError(null);
    appendOptimisticUserMessage(trimmed, { conversationId });

    const knownConversation = conversationsRef.current.find(
      (conversation) => conversation.id === conversationId,
    );
    const resolvedPodId = knownConversation?.pod_id ?? scope.podId;

    try {
      await client.conversations.appendMessage(
        conversationId,
        { content: trimmed, metadata: options.metadata ?? undefined },
        { pod_id: resolvedPodId ?? undefined },
      );
      touchConversation(conversationId, { updated_at: new Date().toISOString() });
      // Reattach whatever stream should be watching this conversation --
      // this is what turns the persisted message into something the user
      // actually sees arrive, whether that's the still-open stream from the
      // turn this joined or a reconnect after it had died. Same pattern
      // `resolveUserApproval` uses after an action that (re)starts a run.
      void sessionResumeIfRunning(conversationId, { expectRun: true }).catch(() => {});
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      setLocalError(err instanceof Error ? err.message : "Failed to send this message");
      throw err;
    }
  }, [
    appendOptimisticUserMessage,
    client,
    enabled,
    scope.podId,
    sessionResumeIfRunning,
    touchConversation,
  ]);

  const retryFailedMessage = useCallback(async () => {
    const conversationId = activeConversationIdRef.current;
    if (!enabled || !conversationId || isStreaming || sessionIsStreaming) return;

    setLocalError(null);
    setIsStreaming(true);
    touchConversation(conversationId, {
      status: "RUNNING" as Conversation["status"],
      last_run_status: "RUNNING" as Conversation["last_run_status"],
      last_run_error: null,
      last_run_retryable: false,
    });
    try {
      await sessionRetryFailedRun(conversationId);
      touchConversation(conversationId, { updated_at: new Date().toISOString() });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      await refreshConversationDetail(conversationId).catch(() => undefined);
      setLocalError(err instanceof Error ? err.message : "Failed to retry message");
    } finally {
      setIsStreaming(false);
    }
  }, [enabled, isStreaming, refreshConversationDetail, sessionIsStreaming, sessionRetryFailedRun, touchConversation]);

  const uploadFiles = useCallback(async (
    files: File[],
    options?: { deferUntilSend?: boolean },
  ) => {
    const normalizedFiles = files.filter((file) => file instanceof File);
    if (!enabled || normalizedFiles.length === 0 || isLoading || isUploadingFiles) return;

    void options;
    setLocalError(null);
    queuePendingFiles(normalizedFiles);
  }, [
    enabled,
    isLoading,
    isUploadingFiles,
    queuePendingFiles,
  ]);

  const resolveUserApproval = useCallback(async (
    approvalId: string,
    decision: AssistantUserApprovalDecision,
    response?: Record<string, unknown> | null,
  ) => {
    if (!enabled) return;
    const conversationId = activeConversationIdRef.current;
    if (!conversationId) {
      throw new Error("An active conversation is required to resolve this approval.");
    }

    const knownConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
    const resolvedPodId = knownConversation?.pod_id ?? scope.podId;
    setLocalError(null);
    try {
      await client.conversations.approvals.resolve(
        conversationId,
        approvalId,
        { decision, response: response ?? {} },
        { pod_id: resolvedPodId ?? undefined },
      );
      await loadConversationMessages(conversationId);
      // Answering is what starts the next run, so this is the one caller that
      // knows one is coming. An approved `request_approval` reconciles in a
      // worker and answers `"queued"`, which means the record can still read
      // WAITING for a moment after this returns — and a single read landing
      // there is how the answer to a question you just answered ended up
      // needing a reload to see.
      // force: true because an Agent Host permission wait never leaves
      // RUNNING (see resumeIfRunning's `force` option), so the ordinary
      // dedup key looks identical whether or not the earlier subscription
      // is still alive. Right after an explicit approval a fresh reconnect
      // attempt is always warranted.
      void sessionResumeIfRunning(conversationId, { expectRun: true, force: true }).catch((error) => {
        setLocalError((prev) => prev || (error instanceof Error ? error.message : "Failed to resume conversation"));
      });
    } catch (err) {
      // The resolve may have partially completed on the server (decision recorded,
      // tool return appended) before the response failed. Reload and only surface
      // the error if the approval is still genuinely pending — otherwise the card
      // self-clears and the user can keep chatting instead of retrying a dead card.
      const items = await loadConversationMessages(conversationId);
      if (approvalResultPresent(items, approvalId)) {
        // The decision did land, so a run is still coming — same race.
        void sessionResumeIfRunning(conversationId, { expectRun: true, force: true }).catch(() => {});
        return;
      }
      setLocalError(err instanceof Error ? err.message : "Failed to resolve approval");
      throw err;
    }
  }, [client, enabled, loadConversationMessages, scope.podId, sessionResumeIfRunning]);

  const { pendingActions, completedActions } = useMemo(() => {
    const pending: AssistantAction[] = [];
    const completed: AssistantAction[] = [];

    messages.forEach((message) => {
      if (!message.toolInvocations) return;
      message.toolInvocations.forEach((toolInvocation) => {
        const status = toolInvocation.state === "result"
          ? (toolInvocation.result?.success === false ? "failed" : "completed")
          : "executing";

        const action: AssistantAction = {
          id: toolInvocation.toolCallId,
          type: "tool_call",
          status,
          toolName: toolInvocation.toolName,
          toolArgs: toolInvocation.args,
          result: toolInvocation.result,
          timestamp: message.createdAt || new Date(),
        };

        if (status === "executing") {
          pending.push(action);
        } else {
          completed.push(action);
        }
      });
    });

    return { pendingActions: pending, completedActions: completed };
  }, [messages]);

  const isActiveConversationRunning = useMemo(() => {
    if (!activeConversationId) return false;
    const activeConversation = conversations.find((conversation) => conversation.id === activeConversationId);
    return isConversationRunning(activeConversation?.status);
  }, [activeConversationId, conversations]);

  return useMemo(() => ({
    messages,
    conversations,
    openedConversationId: activeConversationId,
    activeConversationId,
    availableModels,
    conversationModel,
    conversationRuntime,
    isOpenedConversationRunning: isActiveConversationRunning,
    isActiveConversationRunning,
    isLoading,
    isLoadingConversations,
    isLoadingMoreConversations,
    hasMoreConversations: !!conversationsCursor,
    isLoadingMessages,
    isLoadingOlderMessages,
    hasOlderMessages: !!olderMessagesCursor,
    isUploadingFiles,
    pendingFiles,
    pendingFileUploads,
    error,
    canRetryFailedMessage,
    pendingActions,
    completedActions,
    streamingTool: sessionStreamingTool,
    openConversation,
    closeConversation,
    selectConversation,
    setConversationModel,
    sendMessage,
    steerMessage,
    retryFailedMessage,
    uploadFiles,
    removePendingFile,
    clearPendingFiles,
    loadOlderMessages,
    loadMoreConversations,
    resolveUserApproval,
    clearMessages,
    stop,
  }), [
    activeConversationId,
    availableModels,
    canRetryFailedMessage,
    closeConversation,
    clearMessages,
    clearPendingFiles,
    completedActions,
    conversationRuntime,
    conversationModel,
    conversations,
    error,
    conversationsCursor,
    isActiveConversationRunning,
    isLoading,
    isLoadingConversations,
    isLoadingMoreConversations,
    isLoadingMessages,
    isLoadingOlderMessages,
    isUploadingFiles,
    loadMoreConversations,
    loadOlderMessages,
    messages,
    olderMessagesCursor,
    pendingActions,
    pendingFileUploads,
    pendingFiles,
    openConversation,
    removePendingFile,
    resolveUserApproval,
    retryFailedMessage,
    selectConversation,
    sendMessage,
    steerMessage,
    sessionStreamingTool,
    setConversationModel,
    stop,
    uploadFiles,
  ]);
}
