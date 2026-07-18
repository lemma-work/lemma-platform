import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LemmaClient } from "../client.js";
import { parseSSEJson, readSSE, type SseRawEvent } from "../streams.js";
import type {
  AgentRuntimeConfig,
  Conversation,
  ConversationMessage,
  ConversationModel,
  CursorPage,
} from "../types.js";
import { parseAssistantStreamEvent, upsertConversationMessage } from "../assistant-events.js";
import {
  conversationMessageText,
  getLatestAssistantMessage,
} from "./assistant-output.js";
import { normalizeError } from "./utils.js";

interface ConversationScope {
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

export interface UseAssistantSessionOptions {
  client: LemmaClient;
  podId?: string;
  agentName?: string;
  /**
   * @deprecated Use agentName instead.
   */
  assistantName?: string;
  /**
   * @deprecated Use agentName instead.
   */
  assistantId?: string;
  organizationId?: string;
  instructions?: string | null;
  conversationId?: string | null;
  autoLoad?: boolean;
  autoResume?: boolean;
  syncOnTurnEnd?: boolean;
  onEvent?: (event: SseRawEvent, payload: unknown | null) => void;
  onStatus?: (status: string) => void;
  onMessage?: (message: ConversationMessage) => void;
  onError?: (error: unknown) => void;
}

export interface CreateConversationInput {
  title?: string | null;
  instructions?: string | null;
  metadata?: Record<string, unknown> | null;
  model?: ConversationModel | null;
  agentRuntime?: AgentRuntimeConfig | null;
  podId?: string | null;
  agentName?: string | null;
  /** Parent conversation id for sub-agent (child) conversations. */
  parentId?: string | null;
  /**
   * @deprecated Use agentName instead.
   */
  assistantName?: string | null;
  /**
   * @deprecated Use agentName instead.
   */
  assistantId?: string | null;
  organizationId?: string | null;
  setActive?: boolean;
}

export interface SendAssistantMessageOptions {
  conversationId?: string | null;
  metadata?: Record<string, unknown> | null;
  syncOnTurnEnd?: boolean;
}

export interface ResumeAssistantOptions {
  conversationId?: string | null;
  /**
   * When true, skips resume unless conversation status is currently RUNNING.
   */
  onlyIfRunning?: boolean;
  syncOnTurnEnd?: boolean;
}

export interface AssistantStreamingTool {
  toolCallId?: string;
  toolName: string;
  args?: Record<string, unknown>;
  state: "call" | "result";
  result?: Record<string, unknown>;
}

export interface UseAssistantSessionResult {
  conversationId: string | null;
  conversation: Conversation | null;
  status?: string;
  messages: ConversationMessage[];
  latestAssistantMessage: ConversationMessage | null;
  output: ConversationMessage | null;
  outputText: string;
  finalOutput: ConversationMessage | null;
  finalOutputText: string;
  streamingText: string;
  streamingTool: AssistantStreamingTool | null;
  isStreaming: boolean;
  error: Error | null;
  setConversationId: (conversationId: string | null) => void;
  listConversations: (options?: {
    limit?: number;
    pageToken?: string;
    scope?: ConversationScope;
  }) => Promise<CursorPage<Conversation>>;
  createConversation: (input?: CreateConversationInput) => Promise<Conversation>;
  refreshConversation: (conversationId?: string | null) => Promise<Conversation | null>;
  loadMessages: (options?: {
    conversationId?: string | null;
    limit?: number;
    pageToken?: string;
  }) => Promise<CursorPage<ConversationMessage>>;
  sendMessage: (content: string, options?: SendAssistantMessageOptions) => Promise<Conversation>;
  resume: (conversationId?: string | null | ResumeAssistantOptions) => Promise<void>;
  resumeIfRunning: (conversationId?: string | null) => Promise<boolean>;
  stop: (conversationId?: string | null) => Promise<void>;
  cancel: () => void;
  clearMessages: () => void;
}

function applyPodScope(client: LemmaClient, podId?: string | null): LemmaClient {
  const resolvedPodId = podId ?? client.podId;
  if (resolvedPodId && resolvedPodId !== client.podId) {
    return client.withPod(resolvedPodId);
  }
  return client;
}

function requireConversationId(conversationId?: string | null): string {
  if (!conversationId) {
    throw new Error("conversationId is required.");
  }
  return conversationId;
}

function normalizeScope(
  client: LemmaClient,
  defaults: ConversationScope,
  override?: ConversationScope,
): ConversationScope {
  const resolvedAgentName = override?.agentName
    ?? override?.assistantName
    ?? override?.assistantId
    ?? defaults.agentName
    ?? defaults.assistantName
    ?? defaults.assistantId
    ?? null;

  return {
    podId: override?.podId ?? defaults.podId ?? client.podId ?? null,
    agentName: resolvedAgentName,
    assistantName: override?.assistantName ?? defaults.assistantName ?? null,
    assistantId: override?.assistantId ?? defaults.assistantId ?? null,
    organizationId: override?.organizationId ?? defaults.organizationId ?? null,
  };
}

function normalizeConversationStatus(status: unknown): string | undefined {
  if (typeof status !== "string") return undefined;
  const normalized = status.trim().toUpperCase();
  return normalized.length > 0 ? normalized : undefined;
}

function isConversationRunningStatus(status: unknown): boolean {
  const normalized = normalizeConversationStatus(status);
  if (!normalized) return false;
  return normalized === "RUNNING" || normalized === "IN_PROGRESS" || normalized === "PROCESSING";
}

function waitForStreamReconnect(attempt: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);

  const delayMs = Math.min(2 ** Math.min(Math.max(attempt - 1, 0), 4) * 1000, 10_000);
  return new Promise((resolve) => {
    let timeoutId: ReturnType<typeof setTimeout>;
    const handleAbort = () => {
      clearTimeout(timeoutId);
      resolve(false);
    };
    timeoutId = setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve(true);
    }, delayMs);
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function parseMaybeJsonValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function parseMaybeJsonObject(value: unknown): Record<string, unknown> {
  const parsed = parseMaybeJsonValue(value);
  return isRecord(parsed) ? parsed : {};
}

function normalizeToolResult(value: unknown): Record<string, unknown> {
  const parsed = parseMaybeJsonValue(value);
  if (isRecord(parsed)) return parsed;
  if (Array.isArray(parsed)) return { output: parsed };
  if (typeof parsed === "undefined" || parsed === null) return {};
  return { output: parsed };
}

function parseStreamingToolToken(token: string): AssistantStreamingTool | null {
  const parsed = parseMaybeJsonValue(token);
  if (!isRecord(parsed)) return null;

  const toolName = [parsed.tool_name, parsed.toolName, parsed.name]
    .find((value) => typeof value === "string" && value.trim().length > 0);
  if (typeof toolName !== "string") return null;

  const rawToolCallId = [parsed.tool_call_id, parsed.toolCallId, parsed.call_id, parsed.id]
    .find((value) => typeof value === "string" && value.trim().length > 0);
  const rawArgs = parsed.tool_args ?? parsed.tool_input ?? parsed.args ?? parsed.arguments ?? parsed.input;
  const rawResult = parsed.tool_result ?? parsed.tool_output ?? parsed.result ?? parsed.output;
  const hasResult = typeof rawResult !== "undefined";

  return {
    ...(typeof rawToolCallId === "string" ? { toolCallId: rawToolCallId } : {}),
    toolName,
    args: parseMaybeJsonObject(rawArgs),
    state: hasResult ? "result" : "call",
    ...(hasResult ? { result: normalizeToolResult(rawResult) } : {}),
  };
}

function parsePartialStreamingToolToken(token: string): AssistantStreamingTool | null {
  const toolNameMatch = /"(?:tool_name|toolName|name)"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(token);
  if (!toolNameMatch?.[1]) return null;

  const idMatch = /"(?:tool_call_id|toolCallId|call_id|id)"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(token);
  const unescapeJsonString = (value: string): string => {
    try {
      return JSON.parse(`"${value}"`) as string;
    } catch {
      return value;
    }
  };

  return {
    ...(idMatch?.[1] ? { toolCallId: unescapeJsonString(idMatch[1]) } : {}),
    toolName: unescapeJsonString(toolNameMatch[1]),
    args: {},
    state: "call",
  };
}

function resolveResumeInput(
  input?: string | null | ResumeAssistantOptions,
): ResumeAssistantOptions {
  if (typeof input === "string" || input === null) {
    return { conversationId: input };
  }
  return input ?? {};
}

export function useAssistantSession(options: UseAssistantSessionOptions): UseAssistantSessionResult {
  const {
    client,
    podId: defaultPodId,
    agentName: defaultAgentName,
    assistantName: defaultAssistantName,
    assistantId: defaultAssistantId,
    organizationId: defaultOrganizationId,
    instructions: defaultInstructions,
    conversationId: externalConversationId = null,
    autoLoad = true,
    autoResume = false,
    syncOnTurnEnd = false,
    onEvent,
    onStatus,
    onMessage,
    onError,
  } = options;

  const [conversationId, setConversationIdState] = useState<string | null>(externalConversationId);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [streamingTool, setStreamingTool] = useState<AssistantStreamingTool | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const conversationIdRef = useRef<string | null>(externalConversationId);
  const statusRef = useRef<string | undefined>(undefined);
  const streamingTextRef = useRef("");
  const streamingToolTokenRef = useRef("");
  const autoResumedKeyRef = useRef<string | null>(null);
  const autoLoadInFlightKeyRef = useRef<string | null>(null);
  const lastAutoLoadedKeyRef = useRef<string | null>(null);
  const onEventRef = useRef(onEvent);
  const onStatusRef = useRef(onStatus);
  const onMessageRef = useRef(onMessage);
  const onErrorRef = useRef(onError);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const consumeRef = useRef<(opts: any) => Promise<void>>(null!);
  const streamReconnectCountRef = useRef(0);

  const setConversationId = useCallback((nextConversationId: string | null) => {
    abortRef.current?.abort();
    abortRef.current = null;
    setConversationIdState((currentConversationId) => {
      if (currentConversationId === nextConversationId) {
        return currentConversationId;
      }

      autoResumedKeyRef.current = null;
      autoLoadInFlightKeyRef.current = null;
      lastAutoLoadedKeyRef.current = null;
      streamingTextRef.current = "";
      setStreamingText("");
      setStreamingTool(null);
      setConversation(null);
      setStatus(undefined);
      statusRef.current = undefined;
      setMessages([]);
      setError(null);
      setIsStreaming(false);

      return nextConversationId;
    });
  }, []);

  useEffect(() => {
    setConversationId(externalConversationId);
  }, [externalConversationId, setConversationId]);

  useEffect(() => {
    conversationIdRef.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    onStatusRef.current = onStatus;
  }, [onStatus]);

  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  const setConversationStatus = useCallback((nextStatus?: string) => {
    const normalized = normalizeConversationStatus(nextStatus);
    setStatus(normalized);
    statusRef.current = normalized;
    if (normalized) {
      onStatusRef.current?.(normalized);
    }
  }, []);

  const pendingStreamingFlushRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearStreamingText = useCallback(() => {
    if (pendingStreamingFlushRef.current) {
      clearTimeout(pendingStreamingFlushRef.current);
      pendingStreamingFlushRef.current = null;
    }
    streamingTextRef.current = "";
    setStreamingText("");
  }, []);

  const clearStreamingTool = useCallback(() => {
    streamingToolTokenRef.current = "";
    setStreamingTool(null);
  }, []);

  const appendStreamingToken = useCallback((token: string) => {
    if (!token) return;
    streamingTextRef.current += token;
    if (!pendingStreamingFlushRef.current) {
      pendingStreamingFlushRef.current = setTimeout(() => {
        pendingStreamingFlushRef.current = null;
        setStreamingText(streamingTextRef.current);
      }, 0);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (pendingStreamingFlushRef.current) {
        clearTimeout(pendingStreamingFlushRef.current);
      }
    };
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const defaultScope = useMemo<ConversationScope>(() => ({
    podId: defaultPodId ?? null,
    agentName: defaultAgentName ?? defaultAssistantName ?? defaultAssistantId ?? null,
    assistantName: defaultAssistantName ?? defaultAssistantId ?? null,
    assistantId: defaultAssistantId ?? null,
    organizationId: defaultOrganizationId ?? null,
  }), [defaultAgentName, defaultAssistantId, defaultAssistantName, defaultOrganizationId, defaultPodId]);

  const listConversations = useCallback(async (input: {
    limit?: number;
    pageToken?: string;
    scope?: ConversationScope;
  } = {}): Promise<CursorPage<Conversation>> => {
    setError(null);
    try {
      const scope = normalizeScope(client, defaultScope, input.scope);
      const scopedClient = applyPodScope(client, scope.podId);

      const response = await scopedClient.conversations.list({
        pod_id: scope.podId ?? undefined,
        agent_name: scope.agentName ?? undefined,
        limit: input.limit,
        page_token: input.pageToken,
      });

      return {
        items: response.items ?? [],
        limit: response.limit ?? input.limit ?? 20,
        next_page_token: response.next_page_token,
        total: (response as { total?: number }).total,
      };
    } catch (listError) {
      const normalized = normalizeError(listError, "Failed to list conversations.");
      setError(normalized);
      onErrorRef.current?.(listError);
      return {
        items: [],
        limit: input.limit ?? 20,
        next_page_token: null,
      };
    }
  }, [client, defaultScope]);

  const createConversation = useCallback(async (input: CreateConversationInput = {}): Promise<Conversation> => {
    setError(null);
    try {
      const scopedClient = applyPodScope(client, input.podId ?? defaultPodId ?? null);

      const payload = {
        title: input.title ?? undefined,
        instructions: typeof input.instructions === "undefined"
          ? defaultInstructions ?? undefined
          : input.instructions,
        metadata: input.metadata ?? undefined,
        pod_id: input.podId ?? defaultPodId ?? scopedClient.podId ?? undefined,
        agent_name: input.agentName
          ?? input.assistantName
          ?? input.assistantId
          ?? defaultAgentName
          ?? defaultAssistantName
          ?? defaultAssistantId
          ?? undefined,
        model: typeof input.model === "undefined"
          ? undefined
          : (input.model as unknown as never),
        agent_runtime: typeof input.agentRuntime === "undefined"
          ? undefined
          : input.agentRuntime,
        parent_id: input.parentId ?? undefined,
      };

      const created = await scopedClient.conversations.create(payload);

      if (input.setActive !== false) {
        setConversationIdState(created.id);
        setConversation(created);
        setConversationStatus(created.status ?? undefined);
        setMessages([]);
        clearStreamingText();
        autoResumedKeyRef.current = null;
      }

      return created;
    } catch (createError) {
      const normalized = normalizeError(createError, "Failed to create conversation.");
      setError(normalized);
      onErrorRef.current?.(createError);
      throw normalized;
    }
  }, [
    clearStreamingText,
    client,
    defaultAgentName,
    defaultAssistantId,
    defaultAssistantName,
    defaultInstructions,
    defaultPodId,
    setConversationStatus,
  ]);

  const refreshConversation = useCallback(async (explicitConversationId?: string | null): Promise<Conversation | null> => {
    const id = explicitConversationId ?? conversationId;
    if (!id) return null;

    setError(null);
    try {
      const scope = normalizeScope(client, defaultScope);
      const scopedClient = applyPodScope(client, scope.podId);

      const nextConversation = await scopedClient.conversations.get(id, {
        pod_id: scope.podId ?? undefined,
      });

      setConversation(nextConversation);
      const nextStatus = typeof nextConversation.status === "string"
        ? nextConversation.status
        : undefined;
      setConversationStatus(nextStatus);

      return nextConversation;
    } catch (refreshError) {
      const normalized = normalizeError(refreshError, "Failed to fetch conversation.");
      setError(normalized);
      onErrorRef.current?.(refreshError);
      return null;
    }
  }, [client, conversationId, defaultScope, setConversationStatus]);

  const loadMessages = useCallback(async (input: {
    conversationId?: string | null;
    limit?: number;
    pageToken?: string;
  } = {}): Promise<CursorPage<ConversationMessage>> => {
    const id = input.conversationId ?? conversationId;
    if (!id) {
      return { items: [], limit: input.limit ?? 20, next_page_token: null };
    }

    setError(null);
    try {
      const response = await client.conversations.messages.list(id, {
        limit: input.limit,
        page_token: input.pageToken,
      });

      const nextMessages = response.items ?? [];
      if (conversationIdRef.current !== id) {
        return {
          items: nextMessages,
          limit: response.limit ?? input.limit ?? 20,
          next_page_token: response.next_page_token,
        };
      }

      setMessages((previous) => nextMessages.reduce(
        (accumulator, message) => upsertConversationMessage(accumulator, message),
        previous,
      ));

      return {
        items: nextMessages,
        limit: response.limit ?? input.limit ?? 20,
        next_page_token: response.next_page_token,
      };
    } catch (messageError) {
      const normalized = normalizeError(messageError, "Failed to fetch conversation messages.");
      setError(normalized);
      onErrorRef.current?.(messageError);
      return {
        items: [],
        limit: input.limit ?? 20,
        next_page_token: null,
      };
    }
  }, [clearStreamingText, client, conversationId, defaultScope, setConversationStatus]);

  const consume = useCallback(async ({
    stream,
    controller,
    streamConversationId,
    syncAfterStream,
  }: {
    stream: ReadableStream<Uint8Array>;
    controller: AbortController;
    streamConversationId?: string | null;
    syncAfterStream?: boolean;
  }): Promise<void> => {
    setIsStreaming(true);
    setError(null);
    clearStreamingText();
    let sawTerminalStatus = false;
    let streamFailure: unknown = null;

    try {
      for await (const event of readSSE(stream)) {
        if (controller.signal.aborted) {
          break;
        }

        const payload = parseSSEJson(event);
        onEventRef.current?.(event, payload);

        const parsed = parseAssistantStreamEvent(payload);
        if (parsed.error) {
          const streamError = new Error(parsed.error);
          setError(streamError);
          onErrorRef.current?.(streamError);
          setConversationStatus(parsed.status ?? "FAILED");
          sawTerminalStatus = true;
          clearStreamingText();
          clearStreamingTool();
          continue;
        }
        if (parsed.token) {
          if (parsed.tokenKind === "tool") {
            streamingToolTokenRef.current += parsed.token;
            const tool = parseStreamingToolToken(streamingToolTokenRef.current)
              || parsePartialStreamingToolToken(streamingToolTokenRef.current);
            if (tool?.state === "call") {
              setStreamingTool(tool);
              if (parseStreamingToolToken(streamingToolTokenRef.current)) {
                streamingToolTokenRef.current = "";
              }
            } else if (tool?.state === "result") {
              setStreamingTool((current) => (
                current?.toolCallId && current.toolCallId === tool.toolCallId
                  ? { ...current, ...tool }
                  : current
              ));
              streamingToolTokenRef.current = "";
            }
          } else if (!parsed.tokenKind || parsed.tokenKind === "text") {
            appendStreamingToken(parsed.token);
          }
        }
        if (parsed.message) {
          setMessages((previous) => upsertConversationMessage(previous, parsed.message!));
          onMessageRef.current?.(parsed.message);
          const role = typeof parsed.message.role === "string"
            ? parsed.message.role.toLowerCase()
            : "";
          if (role === "assistant" || role === "tool") {
            clearStreamingText();
            clearStreamingTool();
          }
        }
        if (parsed.status) {
          setConversationStatus(parsed.status);
          if (!isConversationRunningStatus(parsed.status)) {
            sawTerminalStatus = true;
            clearStreamingText();
            clearStreamingTool();
          }
        }
      }

    } catch (streamError) {
      if (!(streamError instanceof Error && streamError.name === "AbortError")) {
        streamFailure = streamError;
      }
    }

    try {
      if (!controller.signal.aborted) {
        const syncConversationId = streamConversationId ?? conversationId;
        if (!sawTerminalStatus && syncConversationId) {
          while (!controller.signal.aborted) {
            const latestConversation = await refreshConversation(syncConversationId);
            await loadMessages({ conversationId: syncConversationId, limit: 100 });
            if (controller.signal.aborted) break;

            const latestStatus = latestConversation?.status ?? statusRef.current;
            if (!isConversationRunningStatus(latestStatus)) {
              streamReconnectCountRef.current = 0;
              streamFailure = null;
              break;
            }

            streamReconnectCountRef.current += 1;
            const shouldReconnect = await waitForStreamReconnect(
              streamReconnectCountRef.current,
              controller.signal,
            );
            if (!shouldReconnect) break;

            try {
              const scope = normalizeScope(client, defaultScope);
              const scopedClient = applyPodScope(client, scope.podId);
              const newStream = await scopedClient.conversations.resumeStream(syncConversationId, {
                pod_id: scope.podId ?? undefined,
                signal: controller.signal,
              });
              streamReconnectCountRef.current = 0;
              return await consumeRef.current({
                stream: newStream,
                controller,
                streamConversationId: syncConversationId,
                syncAfterStream,
              });
            } catch (reconnectError) {
              if (reconnectError instanceof Error && reconnectError.name === "AbortError") break;
              streamFailure = reconnectError;
            }
          }
        } else if (syncConversationId && (syncAfterStream ?? syncOnTurnEnd)) {
          await refreshConversation(syncConversationId);
          await loadMessages({ conversationId: syncConversationId, limit: 100 });
        }

        if (!controller.signal.aborted && streamFailure) {
          const normalized = normalizeError(streamFailure, "Failed to stream conversation.");
          setError(normalized);
          onErrorRef.current?.(streamFailure);
        }
      }
    } finally {
      clearStreamingText();
      clearStreamingTool();
      if (controller.signal.aborted) streamReconnectCountRef.current = 0;
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      setIsStreaming(false);
    }
  }, [
    appendStreamingToken,
    clearStreamingTool,
    clearStreamingText,
    client,
    conversationId,
    defaultScope,
    loadMessages,
    refreshConversation,
    setConversationStatus,
    syncOnTurnEnd,
  ]);

  useEffect(() => {
    consumeRef.current = consume;
  }, [consume]);

  const ensureConversation = useCallback(async (
    overrideConversationId?: string | null,
  ): Promise<Conversation> => {
    const existingId = overrideConversationId ?? conversationId;
    if (existingId) {
      // Avoid a network roundtrip on every send when we already have this conversation in state.
      if (conversation?.id === existingId) {
        return conversation;
      }

      const existing = await refreshConversation(existingId);
      if (existing) return existing;
      throw new Error("Failed to resolve existing conversation.");
    }

    throw new Error("conversationId is required. Create a conversation before sending a message.");
  }, [conversation, conversationId, refreshConversation]);

  const sendMessage = useCallback(async (
    content: string,
    input: SendAssistantMessageOptions = {},
  ): Promise<Conversation> => {
    setError(null);
    try {
      const resolvedConversation = await ensureConversation(input.conversationId);
      const resolvedConversationId = requireConversationId(resolvedConversation.id);

      cancel();
      const controller = new AbortController();
      abortRef.current = controller;

      const scope = normalizeScope(client, defaultScope);
      const scopedClient = applyPodScope(client, scope.podId);

      const stream = await scopedClient.conversations.sendMessageStream(
        resolvedConversationId,
        { content, metadata: input.metadata ?? undefined },
        {
          pod_id: scope.podId ?? undefined,
          signal: controller.signal,
        },
      );

      setConversationStatus("RUNNING");
      await consume({
        stream,
        controller,
        streamConversationId: resolvedConversationId,
        syncAfterStream: input.syncOnTurnEnd,
      });
      return resolvedConversation;
    } catch (sendError) {
      const normalized = normalizeError(sendError, "Failed to send agent message.");
      setError(normalized);
      onErrorRef.current?.(sendError);
      throw normalized;
    }
  }, [cancel, client, consume, defaultScope, ensureConversation, setConversationStatus]);

  const resume = useCallback(async (input?: string | null | ResumeAssistantOptions): Promise<void> => {
    setError(null);
    try {
      const resumeInput = resolveResumeInput(input);
      const id = requireConversationId(resumeInput.conversationId ?? conversationId);

      if (resumeInput.onlyIfRunning && !isConversationRunningStatus(statusRef.current)) {
        return;
      }

      cancel();
      const controller = new AbortController();
      abortRef.current = controller;

      const scope = normalizeScope(client, defaultScope);
      const scopedClient = applyPodScope(client, scope.podId);

      const stream = await scopedClient.conversations.resumeStream(id, {
        pod_id: scope.podId ?? undefined,
        signal: controller.signal,
      });

      setConversationStatus("RUNNING");
      await consume({
        stream,
        controller,
        streamConversationId: id,
        syncAfterStream: resumeInput.syncOnTurnEnd,
      });
    } catch (resumeError) {
      const normalized = normalizeError(resumeError, "Failed to resume conversation.");
      setError(normalized);
      onErrorRef.current?.(resumeError);
      throw normalized;
    }
  }, [cancel, client, consume, conversationId, defaultScope, setConversationStatus]);

  const resumeIfRunning = useCallback(async (explicitConversationId?: string | null): Promise<boolean> => {
    const id = explicitConversationId ?? conversationId;
    if (!id) return false;
    if (isStreaming) return false;

    const statusKey = normalizeConversationStatus(statusRef.current);
    const resumeKey = `${id}:${statusKey ?? "UNKNOWN"}`;
    if (autoResumedKeyRef.current === resumeKey) {
      return false;
    }

    const knownRunning = isConversationRunningStatus(statusRef.current);
    if (!knownRunning) {
      const latestConversation = await refreshConversation(id);
      if (!latestConversation || !isConversationRunningStatus(latestConversation.status)) {
        return false;
      }
    }

    const previousResumeKey = autoResumedKeyRef.current;
    autoResumedKeyRef.current = resumeKey;
    try {
      await resume({
        conversationId: id,
        onlyIfRunning: true,
      });
      return true;
    } catch (error) {
      if (autoResumedKeyRef.current === resumeKey) {
        autoResumedKeyRef.current = previousResumeKey;
      }
      throw error;
    }
  }, [conversationId, isStreaming, refreshConversation, resume]);

  const stop = useCallback(async (explicitConversationId?: string | null): Promise<void> => {
    setError(null);
    try {
      const id = requireConversationId(explicitConversationId ?? conversationId);

      const scope = normalizeScope(client, defaultScope);
      const scopedClient = applyPodScope(client, scope.podId);

      await scopedClient.conversations.stopRun(id, {
        pod_id: scope.podId ?? undefined,
      });
      setConversationStatus("WAITING");
      clearStreamingText();
    } catch (stopError) {
      const normalized = normalizeError(stopError, "Failed to stop conversation.");
      setError(normalized);
      onErrorRef.current?.(stopError);
      throw normalized;
    }
  }, [client, conversationId, defaultScope]);

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  useEffect(() => {
    autoResumedKeyRef.current = null;
  }, [conversationId]);

  useEffect(() => {
    if (!isConversationRunningStatus(status)) {
      autoResumedKeyRef.current = null;
    }
  }, [status]);

  useEffect(() => {
    if (!autoLoad || !conversationId) {
      autoLoadInFlightKeyRef.current = null;
      lastAutoLoadedKeyRef.current = null;
      return;
    }

    const bootstrapKey = `${conversationId}:${autoResume ? "resume" : "load"}`;
    if (
      autoLoadInFlightKeyRef.current === bootstrapKey
      || lastAutoLoadedKeyRef.current === bootstrapKey
    ) {
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    autoLoadInFlightKeyRef.current = bootstrapKey;

    const bootstrapConversation = async () => {
      const latestConversation = await refreshConversation(conversationId);
      if (cancelled) return;

      await loadMessages({ conversationId, limit: 100 });
      if (cancelled) return;

      if (!autoResume) return;
      const latestStatus = normalizeConversationStatus(latestConversation?.status) ?? normalizeConversationStatus(statusRef.current);
      if (!isConversationRunningStatus(latestStatus)) return;
      await resumeIfRunning(conversationId);
    };

    void bootstrapConversation()
      .catch((bootstrapError) => {
        if (cancelled) return;
        const normalized = normalizeError(bootstrapError, "Failed to load agent conversation.");
        setError(normalized);
        onErrorRef.current?.(bootstrapError);
      })
      .finally(() => {
        if (autoLoadInFlightKeyRef.current === bootstrapKey) {
          autoLoadInFlightKeyRef.current = null;
        }
        lastAutoLoadedKeyRef.current = bootstrapKey;
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [autoLoad, autoResume, conversationId, loadMessages, refreshConversation, resumeIfRunning]);

  const latestAssistantMessage = useMemo(
    () => getLatestAssistantMessage(messages),
    [messages],
  );
  const output = latestAssistantMessage ?? null;
  const latestAssistantText = conversationMessageText(latestAssistantMessage);
  const outputText = streamingText.trim() || latestAssistantText;
  const finalOutput = !isStreaming && !isConversationRunningStatus(status) ? output : null;
  const finalOutputText = !isStreaming && !isConversationRunningStatus(status) ? latestAssistantText : "";

  return {
    conversationId,
    conversation,
    status,
    messages,
    latestAssistantMessage,
    output,
    outputText,
    finalOutput,
    finalOutputText,
    streamingText,
    streamingTool,
    isStreaming,
    error,
    setConversationId,
    listConversations,
    createConversation,
    refreshConversation,
    loadMessages,
    sendMessage,
    resume,
    resumeIfRunning,
    stop,
    cancel,
    clearMessages,
  };
}
