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
  metadata?: (Record<string, unknown> & AssistantMessageMetadata) | null;
  message_metadata?: AssistantMessageMetadata;
  tool_calls?: Record<string, unknown>[];
};

const CONVERSATIONS_PAGE_SIZE = 30;

const EMPTY_HISTORY_SCOPE_KEY = JSON.stringify({ podId: null });

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
  options?: { thinkingDurationMs?: number },
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
      durationMs: thinkingPart.durationMs ?? options?.thinkingDurationMs,
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

function mapConversationMessages(messages: AssistantApiConversationMessage[]): AssistantRenderableMessage[] {
  const mappedMessages: AssistantRenderableMessage[] = [];
  const pendingToolCalls = new Map<string, AssistantToolInvocation>();

  const estimateThinkingDurationMs = (index: number): number | undefined => {
    const message = messages[index];
    if (!message || !extractThinkingPart(message)) return undefined;

    const startedAtMs = parseTimestampMs(message.created_at);
    if (!startedAtMs) return undefined;

    for (let i = index + 1; i < messages.length; i += 1) {
      const nextCreatedAtMs = parseTimestampMs(messages[i]?.created_at);
      if (!nextCreatedAtMs || nextCreatedAtMs <= startedAtMs) continue;

      const durationMs = nextCreatedAtMs - startedAtMs;
      if (durationMs > 0 && durationMs <= 30 * 60 * 1000) {
        return durationMs;
      }
      break;
    }

    return undefined;
  };

  messages.forEach((rawMessage, index) => {
    const mappedMessage = mapConversationMessage(rawMessage, {
      thinkingDurationMs: estimateThinkingDurationMs(index),
    });

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

  const activeConversationIdRef = useRef<string | null>(null);
  const conversationsRef = useRef<Conversation[]>([]);
  const isStreamingRef = useRef(false);
  const sessionIsStreamingRef = useRef(false);
  const lastAutoLoadedConversationIdRef = useRef<string | null>(null);
  const loadingConversationIdRef = useRef<string | null>(null);
  const skipInitialLoadConversationIdsRef = useRef<Set<string>>(new Set());
  const loadConversationMessagesRef = useRef<((conversationId: string) => Promise<AssistantApiConversationMessage[] | null>) | null>(null);
  const resumeIfRunningRef = useRef<((conversationId?: string | null) => Promise<boolean>) | null>(null);

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
  const historyScopeKey = useMemo(
    () => JSON.stringify({ podId: scope.podId ?? client.podId ?? null }),
    [client.podId, scope.podId],
  );
  const previousHistoryScopeKeyRef = useRef(historyScopeKey);

  const handleAssistantSessionError = useCallback((sessionError: unknown) => {
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
    streamingTool: sessionStreamingTool,
    status: sessionStatus,
  } = assistantSession;

  const {
    runtimeMessages,
    appendOptimisticUserMessage,
    replaceLoadedMessages,
    mergeMessages,
    clear: clearRuntimeMessages,
  } = useAssistantRuntime({
    conversationId: activeConversationId,
    sessionConversationId,
    sessionMessages,
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
    const detail = await client.conversations.get(conversationId, {
      pod_id: knownConversation?.pod_id ?? scope.podId ?? undefined,
    });
    setConversations((previous) => sortConversationsByUpdatedAt([
      detail,
      ...previous.filter((conversation) => conversation.id !== detail.id),
    ]));
    return detail;
  }, [client, scope.podId]);

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
      limit: input.limit,
      page_token: input.pageToken,
    });
  }, [client, scope.podId]);

  const loadConversations = useCallback(async () => {
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
    } catch (err) {
      setLocalError((prev) => prev || (err instanceof Error ? err.message : "Failed to load conversations"));
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

  const loadAvailableModels = useCallback(async (): Promise<AvailableModelInfo[]> => {
    try {
      const response = await client.conversations.listModels({
        orgId: scope.organizationId ?? undefined,
      });
      return response.items ?? [];
    } catch {
      return [];
    }
  }, [client, scope.organizationId]);

  const loadConversationMessages = useCallback(async (
    conversationId: string,
  ): Promise<AssistantApiConversationMessage[] | null> => {
    setIsLoadingMessages(true);
    try {
      const response = await sessionLoadMessages({
        conversationId,
        limit: 100,
      });
      if (activeConversationIdRef.current !== conversationId) {
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
    resumeIfRunningRef.current = sessionResumeIfRunning;
  }, [sessionResumeIfRunning]);

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
      setAvailableModels([]);
      return;
    }
    if (!autoLoad) return;

    let cancelled = false;
    void loadAvailableModels()
      .then((models) => {
        if (cancelled) return;
        setAvailableModels(models);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [autoLoad, enabled, loadAvailableModels]);

  const messages = useMemo(() => {
    if (!activeConversationId) return [];

    const normalized = sortMessagesByCreatedAt(runtimeMessages as AssistantApiConversationMessage[])
      .filter((message) => message.conversation_id === activeConversationId);
    if (normalized.length === 0) return [];

    const nextMessages = mapConversationMessages(normalized);
    const pendingText = sessionStreamingText.trim();
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
  }, [activeConversationId, runtimeMessages, sessionStreamingText]);

  useEffect(() => {
    if (!sessionConversation || sessionConversation.id !== activeConversationId) return;
    setConversations((previous) => sortConversationsByUpdatedAt([
      sessionConversation,
      ...previous.filter((conversation) => conversation.id !== sessionConversation.id),
    ]));
  }, [activeConversationId, sessionConversation]);

  useEffect(() => {
    if (!activeConversationId) return;
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
  }, [activeConversationId, sessionStatus, touchConversation]);

  useEffect(() => {
    if (!activeConversationId) return;
    const activeConversation = conversations.find((conversation) => conversation.id === activeConversationId);
    if (!activeConversation) return;
    setConversationModelState(activeConversation.model ?? null);
    setConversationRuntimeState(activeConversation.agent_runtime ?? null);
  }, [activeConversationId, conversations]);

  useEffect(() => {
    const historyScopeChanged = previousHistoryScopeKeyRef.current !== historyScopeKey;
    previousHistoryScopeKeyRef.current = historyScopeKey;

    if (!enabled) {
      sessionCancel();
      clearRuntimeMessages();
      activeConversationIdRef.current = null;
      lastAutoLoadedConversationIdRef.current = null;
      loadingConversationIdRef.current = null;
      skipInitialLoadConversationIdsRef.current.clear();
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

    activeConversationIdRef.current = null;
    lastAutoLoadedConversationIdRef.current = null;
    loadingConversationIdRef.current = null;
    skipInitialLoadConversationIdsRef.current.clear();
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
    if (!enabled || !autoLoad || historyScopeKey === EMPTY_HISTORY_SCOPE_KEY) return;
    void loadConversations();
  }, [autoLoad, enabled, historyScopeKey, loadConversations]);

  useEffect(() => {
    if (!enabled || !activeConversationId) {
      clearRuntimeMessages();
      lastAutoLoadedConversationIdRef.current = null;
      loadingConversationIdRef.current = null;
      setOlderMessagesCursor(null);
      setIsLoadingMessages(false);
      return;
    }

    if (!autoLoadMessages) {
      clearRuntimeMessages();
      lastAutoLoadedConversationIdRef.current = null;
      loadingConversationIdRef.current = null;
      setOlderMessagesCursor(null);
      setIsLoadingMessages(false);
      return;
    }

    if (skipInitialLoadConversationIdsRef.current.has(activeConversationId)) {
      skipInitialLoadConversationIdsRef.current.delete(activeConversationId);
      lastAutoLoadedConversationIdRef.current = activeConversationId;
      return;
    }

    if (lastAutoLoadedConversationIdRef.current === activeConversationId) {
      return;
    }
    if (loadingConversationIdRef.current === activeConversationId) {
      return;
    }

    let cancelled = false;
    loadingConversationIdRef.current = activeConversationId;
    const loadConversation = async () => {
      setOlderMessagesCursor(null);
      await loadConversationMessagesRef.current?.(activeConversationId);
      if (cancelled) return;
      lastAutoLoadedConversationIdRef.current = activeConversationId;
      try {
        await resumeIfRunningRef.current?.(activeConversationId);
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
    touchConversation(conversationId, { status: "waiting" as Conversation["status"] });
    void sessionStop(conversationId).catch((error) => {
      touchConversation(conversationId, { status: previousStatus });
      setLocalError((prev) => prev || (error instanceof Error ? error.message : "Failed to stop conversation"));
    });
  }, [sessionCancel, sessionStop, touchConversation]);

  const selectConversation = useCallback((conversationId: string | null) => {
    if (sessionIsStreamingRef.current || isStreamingRef.current) {
      sessionCancel();
      setIsStreaming(false);
    }

    const currentConversationId = activeConversationIdRef.current;
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
        || lastAutoLoadedConversationIdRef.current === conversationId
      ) {
        return;
      }

      loadingConversationIdRef.current = conversationId;
      setLocalError(null);
      setOlderMessagesCursor(null);
      setIsLoadingMessages(true);
      void loadConversationMessagesRef.current?.(conversationId)
        .then(() => resumeIfRunningRef.current?.(conversationId))
        .catch((error) => {
          setLocalError((prev) => prev || (error instanceof Error ? error.message : "Failed to resume conversation"));
        })
        .finally(() => {
          if (loadingConversationIdRef.current === conversationId) {
            loadingConversationIdRef.current = null;
          }
          lastAutoLoadedConversationIdRef.current = conversationId;
        });
      return;
    }

    setLocalError(null);
    activeConversationIdRef.current = conversationId;
    lastAutoLoadedConversationIdRef.current = null;
    loadingConversationIdRef.current = null;
    setOlderMessagesCursor(null);
    clearRuntimeMessages();
    setIsLoadingMessages(Boolean(conversationId && autoLoadMessages));
    setActiveConversationId(conversationId);
  }, [autoLoadMessages, clearRuntimeMessages, refreshConversationDetail, sessionCancel]);

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
    lastAutoLoadedConversationIdRef.current = null;
    loadingConversationIdRef.current = null;
    skipInitialLoadConversationIdsRef.current.clear();
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
      title: titleSeed.slice(0, 120),
      instructions: typeof options.instructions === "undefined" ? instructions : options.instructions,
      metadata: options.metadata ?? undefined,
      model: conversationModel as unknown as never,
      agentRuntime: conversationRuntime,
      ...scope,
    });

    setConversations((prev) => sortConversationsByUpdatedAt([
      createdConversation,
      ...prev.filter((conversation) => conversation.id !== createdConversation.id),
    ]));
    activeConversationIdRef.current = createdConversation.id;
    lastAutoLoadedConversationIdRef.current = createdConversation.id;
    loadingConversationIdRef.current = null;
    skipInitialLoadConversationIdsRef.current.add(createdConversation.id);
    setActiveConversationId(createdConversation.id);
    setConversationModelState((createdConversation.model ?? conversationModel ?? null) as ConversationModel | null);
    setConversationRuntimeState(createdConversation.agent_runtime ?? conversationRuntime ?? null);
    clearRuntimeMessages();
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
    try {
      if (!conversationId) {
        conversationId = await ensureConversation(trimmed, {
          instructions: options.instructions,
          metadata: options.conversationMetadata,
        });
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

      appendOptimisticUserMessage(messageContent, {
        conversationId: finalConversationId,
      });

      setIsStreaming(true);
      touchConversation(finalConversationId, {
        status: "running" as Conversation["status"],
        last_run_status: "RUNNING" as Conversation["last_run_status"],
        last_run_error: null,
        last_run_retryable: false,
      });
      await sessionSendMessage(messageContent, {
        conversationId: finalConversationId,
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
    appendOptimisticUserMessage,
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
      void sessionResumeIfRunning(conversationId).catch((error) => {
        setLocalError((prev) => prev || (error instanceof Error ? error.message : "Failed to resume conversation"));
      });
    } catch (err) {
      // The resolve may have partially completed on the server (decision recorded,
      // tool return appended) before the response failed. Reload and only surface
      // the error if the approval is still genuinely pending — otherwise the card
      // self-clears and the user can keep chatting instead of retrying a dead card.
      const items = await loadConversationMessages(conversationId);
      if (approvalResultPresent(items, approvalId)) {
        void sessionResumeIfRunning(conversationId).catch(() => {});
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
    sessionStreamingTool,
    setConversationModel,
    stop,
    uploadFiles,
  ]);
}
