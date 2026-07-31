// Framework-agnostic agent conversation controller.
//
// This is the state machine extracted from the `useAssistantSession` React hook:
// streaming SSE consumption, tool-token parsing, reconnect-with-backoff,
// create/load/send/resume/stop, and conversation status tracking — with NO React
// and NO DOM. It exposes an observable store (`subscribe`/`getState`) so it can
// drive the React hooks today and vanilla/web-component surfaces next, from one
// implementation. The React hook is now a thin adapter over this class.
import type { LemmaClient } from "../../client.js";
import { parseSSEJson, readSSE, type SseRawEvent } from "../../streams.js";
import type {
  AgentRuntimeConfig,
  Conversation,
  ConversationMessage,
  ConversationModel,
  CursorPage,
} from "../../types.js";
import { parseAssistantStreamEvent, upsertConversationMessage } from "../../assistant-events.js";
import {
  conversationMessageText,
  getLatestAssistantMessage,
  isConversationRunningStatus,
  normalizeConversationStatus,
} from "./messages.js";

export interface ConversationScope {
  podId?: string | null;
  agentName?: string | null;
  /** @deprecated Use agentName instead. */
  assistantName?: string | null;
  /** @deprecated Use agentName instead. */
  assistantId?: string | null;
  organizationId?: string | null;
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
  /** @deprecated Use agentName instead. */
  assistantName?: string | null;
  /** @deprecated Use agentName instead. */
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
  /** When true, skips resume unless conversation status is currently RUNNING. */
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

/** Observable, render-agnostic snapshot of a single agent conversation. */
export interface AgentSessionState {
  conversationId: string | null;
  conversation: Conversation | null;
  status?: string;
  messages: ConversationMessage[];
  streamingText: string;
  streamingTool: AssistantStreamingTool | null;
  isStreaming: boolean;
  error: Error | null;
}

export interface AgentControllerOptions {
  client: LemmaClient;
  /** Default pod/agent scope applied when a call doesn't override it. */
  scope?: ConversationScope;
  instructions?: string | null;
  syncOnTurnEnd?: boolean;
  /** Conversation to start attached to (mirrors the hook's `conversationId` prop). */
  initialConversationId?: string | null;
  onEvent?: (event: SseRawEvent, payload: unknown | null) => void;
  onStatus?: (status: string) => void;
  onMessage?: (message: ConversationMessage) => void;
  onError?: (error: unknown) => void;
}

/** Derived, render-ready outputs computed from a session snapshot. */
export interface AgentOutputs {
  latestAssistantMessage: ConversationMessage | null;
  output: ConversationMessage | null;
  outputText: string;
  finalOutput: ConversationMessage | null;
  finalOutputText: string;
}

// --- pure helpers (ported verbatim from the hook) ---------------------------

function normalizeError(error: unknown, fallback: string): Error {
  if (error instanceof Error) return error;
  if (typeof error === "string" && error.trim()) return new Error(error.trim());
  return new Error(fallback);
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

/**
 * Compute the render-ready outputs (latest assistant message, streaming/settled
 * text, final answer) from a session snapshot. Pure — shared by every surface so
 * "what is the answer" is identical in a hook, a web component, or the product.
 */
export function selectAgentOutputs(state: AgentSessionState): AgentOutputs {
  const latestAssistantMessage = getLatestAssistantMessage(state.messages);
  const output = latestAssistantMessage ?? null;
  const latestAssistantText = conversationMessageText(latestAssistantMessage);
  const settled = !state.isStreaming && !isConversationRunningStatus(state.status);
  return {
    latestAssistantMessage,
    output,
    outputText: state.streamingText.trim() || latestAssistantText,
    finalOutput: settled ? output : null,
    finalOutputText: settled ? latestAssistantText : "",
  };
}

// --- the controller ---------------------------------------------------------

export class AgentController {
  private options: AgentControllerOptions;
  private state: AgentSessionState;
  private readonly listeners = new Set<() => void>();

  private abort: AbortController | null = null;
  private streamingBuffer = "";
  private streamingToolToken = "";
  private autoResumedKey: string | null = null;
  private streamReconnectCount = 0;
  private pendingFlush: ReturnType<typeof setTimeout> | null = null;

  constructor(options: AgentControllerOptions) {
    this.options = options;
    this.state = {
      conversationId: options.initialConversationId ?? null,
      conversation: null,
      status: undefined,
      messages: [],
      streamingText: "",
      streamingTool: null,
      isStreaming: false,
      error: null,
    };
  }

  // -- store ------------------------------------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Stable snapshot for `useSyncExternalStore`: identity changes only on patch. */
  getState = (): AgentSessionState => this.state;

  /** Replace the live options (client/scope/callbacks); does not emit. */
  setOptions = (options: AgentControllerOptions): void => {
    this.options = options;
  };

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  private patch(partial: Partial<AgentSessionState>): void {
    this.state = { ...this.state, ...partial };
    this.emit();
  }

  private get client(): LemmaClient {
    return this.options.client;
  }

  private get scopeDefaults(): ConversationScope {
    return this.options.scope ?? {};
  }

  private setConversationStatus(nextStatus?: string): void {
    const normalized = normalizeConversationStatus(nextStatus);
    this.patch({ status: normalized });
    if (normalized) {
      this.options.onStatus?.(normalized);
    }
  }

  // -- streaming text buffering ----------------------------------------------

  private appendStreamingToken(token: string): void {
    if (!token) return;
    this.streamingBuffer += token;
    if (!this.pendingFlush) {
      this.pendingFlush = setTimeout(() => {
        this.pendingFlush = null;
        this.patch({ streamingText: this.streamingBuffer });
      }, 0);
    }
  }

  private clearStreamingText(): void {
    if (this.pendingFlush) {
      clearTimeout(this.pendingFlush);
      this.pendingFlush = null;
    }
    this.streamingBuffer = "";
    this.patch({ streamingText: "" });
  }

  private clearStreamingTool(): void {
    this.streamingToolToken = "";
    this.patch({ streamingTool: null });
  }

  // -- lifecycle --------------------------------------------------------------

  cancel = (): void => {
    this.abort?.abort();
    this.abort = null;
  };

  /** Abort any active stream and clear timers. Call when the owner unmounts. */
  destroy = (): void => {
    this.abort?.abort();
    this.abort = null;
    if (this.pendingFlush) {
      clearTimeout(this.pendingFlush);
      this.pendingFlush = null;
    }
    this.listeners.clear();
  };

  setConversationId = (nextConversationId: string | null): void => {
    this.abort?.abort();
    this.abort = null;
    if (this.state.conversationId === nextConversationId) {
      return;
    }
    this.autoResumedKey = null;
    this.streamingBuffer = "";
    this.streamingToolToken = "";
    this.streamReconnectCount = 0;
    if (this.pendingFlush) {
      clearTimeout(this.pendingFlush);
      this.pendingFlush = null;
    }
    this.patch({
      conversationId: nextConversationId,
      conversation: null,
      status: undefined,
      messages: [],
      streamingText: "",
      streamingTool: null,
      isStreaming: false,
      error: null,
    });
  };

  clearMessages = (): void => {
    this.patch({ messages: [] });
  };

  // -- reads ------------------------------------------------------------------

  listConversations = async (input: {
    limit?: number;
    pageToken?: string;
    scope?: ConversationScope;
  } = {}): Promise<CursorPage<Conversation>> => {
    this.patch({ error: null });
    try {
      const scope = normalizeScope(this.client, this.scopeDefaults, input.scope);
      const scopedClient = applyPodScope(this.client, scope.podId);

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
      this.patch({ error: normalized });
      this.options.onError?.(listError);
      return { items: [], limit: input.limit ?? 20, next_page_token: null };
    }
  };

  createConversation = async (input: CreateConversationInput = {}): Promise<Conversation> => {
    this.patch({ error: null });
    try {
      const scopedClient = applyPodScope(this.client, input.podId ?? this.scopeDefaults.podId ?? null);

      const payload = {
        title: input.title ?? undefined,
        instructions: typeof input.instructions === "undefined"
          ? this.options.instructions ?? undefined
          : input.instructions,
        metadata: input.metadata ?? undefined,
        pod_id: input.podId ?? this.scopeDefaults.podId ?? scopedClient.podId ?? undefined,
        agent_name: input.agentName
          ?? input.assistantName
          ?? input.assistantId
          ?? this.scopeDefaults.agentName
          ?? this.scopeDefaults.assistantName
          ?? this.scopeDefaults.assistantId
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
        this.autoResumedKey = null;
        this.streamingBuffer = "";
        if (this.pendingFlush) {
          clearTimeout(this.pendingFlush);
          this.pendingFlush = null;
        }
        this.patch({
          conversationId: created.id,
          conversation: created,
          messages: [],
          streamingText: "",
        });
        this.setConversationStatus(created.status ?? undefined);
      }

      return created;
    } catch (createError) {
      const normalized = normalizeError(createError, "Failed to create conversation.");
      this.patch({ error: normalized });
      this.options.onError?.(createError);
      throw normalized;
    }
  };

  refreshConversation = async (explicitConversationId?: string | null): Promise<Conversation | null> => {
    const id = explicitConversationId ?? this.state.conversationId;
    if (!id) return null;

    this.patch({ error: null });
    try {
      const scope = normalizeScope(this.client, this.scopeDefaults);
      const scopedClient = applyPodScope(this.client, scope.podId);

      const nextConversation = await scopedClient.conversations.get(id, {
        pod_id: scope.podId ?? undefined,
      });

      this.patch({ conversation: nextConversation });
      this.setConversationStatus(
        typeof nextConversation.status === "string" ? nextConversation.status : undefined,
      );
      return nextConversation;
    } catch (refreshError) {
      const normalized = normalizeError(refreshError, "Failed to fetch conversation.");
      this.patch({ error: normalized });
      this.options.onError?.(refreshError);
      return null;
    }
  };

  loadMessages = async (input: {
    conversationId?: string | null;
    limit?: number;
    pageToken?: string;
  } = {}): Promise<CursorPage<ConversationMessage>> => {
    const id = input.conversationId ?? this.state.conversationId;
    if (!id) {
      return { items: [], limit: input.limit ?? 20, next_page_token: null };
    }

    this.patch({ error: null });
    try {
      const response = await this.client.conversations.messages.list(id, {
        limit: input.limit,
        page_token: input.pageToken,
      });

      const nextMessages = response.items ?? [];
      if (this.state.conversationId !== id) {
        return {
          items: nextMessages,
          limit: response.limit ?? input.limit ?? 20,
          next_page_token: response.next_page_token,
        };
      }

      this.patch({
        messages: nextMessages.reduce(
          (accumulator, message) => upsertConversationMessage(accumulator, message),
          this.state.messages,
        ),
      });

      return {
        items: nextMessages,
        limit: response.limit ?? input.limit ?? 20,
        next_page_token: response.next_page_token,
      };
    } catch (messageError) {
      const normalized = normalizeError(messageError, "Failed to fetch conversation messages.");
      this.patch({ error: normalized });
      this.options.onError?.(messageError);
      return { items: [], limit: input.limit ?? 20, next_page_token: null };
    }
  };

  // -- streaming --------------------------------------------------------------

  private consume = async ({
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
    this.patch({ isStreaming: true, error: null });
    this.clearStreamingText();
    let sawTerminalStatus = false;
    let streamFailure: unknown = null;

    try {
      for await (const event of readSSE(stream)) {
        if (controller.signal.aborted) break;

        const payload = parseSSEJson(event);
        this.options.onEvent?.(event, payload);

        const parsed = parseAssistantStreamEvent(payload);
        if (parsed.error) {
          const streamError = new Error(parsed.error);
          this.patch({ error: streamError });
          this.options.onError?.(streamError);
          this.setConversationStatus(parsed.status ?? "FAILED");
          sawTerminalStatus = true;
          this.clearStreamingText();
          this.clearStreamingTool();
          continue;
        }
        if (parsed.token) {
          if (parsed.tokenKind === "tool") {
            this.streamingToolToken += parsed.token;
            const tool = parseStreamingToolToken(this.streamingToolToken)
              || parsePartialStreamingToolToken(this.streamingToolToken);
            if (tool?.state === "call") {
              this.patch({ streamingTool: tool });
              if (parseStreamingToolToken(this.streamingToolToken)) {
                this.streamingToolToken = "";
              }
            } else if (tool?.state === "result") {
              const current = this.state.streamingTool;
              this.patch({
                streamingTool: current?.toolCallId && current.toolCallId === tool.toolCallId
                  ? { ...current, ...tool }
                  : current,
              });
              this.streamingToolToken = "";
            }
          } else if (!parsed.tokenKind || parsed.tokenKind === "text") {
            this.appendStreamingToken(parsed.token);
          }
        }
        if (parsed.message) {
          this.patch({ messages: upsertConversationMessage(this.state.messages, parsed.message) });
          this.options.onMessage?.(parsed.message);
          const role = typeof parsed.message.role === "string"
            ? parsed.message.role.toLowerCase()
            : "";
          if (role === "assistant" || role === "tool") {
            this.clearStreamingText();
            this.clearStreamingTool();
          }
        }
        if (parsed.status) {
          this.setConversationStatus(parsed.status);
          if (!isConversationRunningStatus(parsed.status)) {
            sawTerminalStatus = true;
            this.clearStreamingText();
            this.clearStreamingTool();
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
        const syncConversationId = streamConversationId ?? this.state.conversationId;
        if (!sawTerminalStatus && syncConversationId) {
          while (!controller.signal.aborted) {
            const latestConversation = await this.refreshConversation(syncConversationId);
            await this.loadMessages({ conversationId: syncConversationId, limit: 100 });
            if (controller.signal.aborted) break;

            const latestStatus = latestConversation?.status ?? this.state.status;
            if (!isConversationRunningStatus(latestStatus)) {
              this.streamReconnectCount = 0;
              streamFailure = null;
              break;
            }

            this.streamReconnectCount += 1;
            const shouldReconnect = await waitForStreamReconnect(
              this.streamReconnectCount,
              controller.signal,
            );
            if (!shouldReconnect) break;

            try {
              const scope = normalizeScope(this.client, this.scopeDefaults);
              const scopedClient = applyPodScope(this.client, scope.podId);
              const newStream = await scopedClient.conversations.resumeStream(syncConversationId, {
                pod_id: scope.podId ?? undefined,
                signal: controller.signal,
              });
              this.streamReconnectCount = 0;
              return await this.consume({
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
        } else if (syncConversationId && (syncAfterStream ?? this.options.syncOnTurnEnd)) {
          await this.refreshConversation(syncConversationId);
          await this.loadMessages({ conversationId: syncConversationId, limit: 100 });
        }

        if (!controller.signal.aborted && streamFailure) {
          const normalized = normalizeError(streamFailure, "Failed to stream conversation.");
          this.patch({ error: normalized });
          this.options.onError?.(streamFailure);
        }
      }
    } finally {
      this.clearStreamingText();
      this.clearStreamingTool();
      if (controller.signal.aborted) this.streamReconnectCount = 0;
      if (this.abort === controller) {
        this.abort = null;
      }
      this.patch({ isStreaming: false });
    }
  };

  private async ensureConversation(overrideConversationId?: string | null): Promise<Conversation> {
    const existingId = overrideConversationId ?? this.state.conversationId;
    if (existingId) {
      // Avoid a network roundtrip on every send when we already hold it in state.
      if (this.state.conversation?.id === existingId) {
        return this.state.conversation;
      }
      const existing = await this.refreshConversation(existingId);
      if (existing) return existing;
      throw new Error("Failed to resolve existing conversation.");
    }
    throw new Error("conversationId is required. Create a conversation before sending a message.");
  }

  sendMessage = async (
    content: string,
    input: SendAssistantMessageOptions = {},
  ): Promise<Conversation> => {
    this.patch({ error: null });
    try {
      const resolvedConversation = await this.ensureConversation(input.conversationId);
      const resolvedConversationId = requireConversationId(resolvedConversation.id);

      this.cancel();
      const controller = new AbortController();
      this.abort = controller;

      const scope = normalizeScope(this.client, this.scopeDefaults);
      const scopedClient = applyPodScope(this.client, scope.podId);

      const stream = await scopedClient.conversations.sendMessageStream(
        resolvedConversationId,
        { content, metadata: input.metadata ?? undefined },
        { pod_id: scope.podId ?? undefined, signal: controller.signal },
      );

      this.setConversationStatus("RUNNING");
      await this.consume({
        stream,
        controller,
        streamConversationId: resolvedConversationId,
        syncAfterStream: input.syncOnTurnEnd,
      });
      return resolvedConversation;
    } catch (sendError) {
      const normalized = normalizeError(sendError, "Failed to send agent message.");
      this.patch({ error: normalized });
      this.options.onError?.(sendError);
      throw normalized;
    }
  };

  resume = async (input?: string | null | ResumeAssistantOptions): Promise<void> => {
    this.patch({ error: null });
    try {
      const resumeInput = resolveResumeInput(input);
      const id = requireConversationId(resumeInput.conversationId ?? this.state.conversationId);

      if (resumeInput.onlyIfRunning && !isConversationRunningStatus(this.state.status)) {
        return;
      }

      this.cancel();
      const controller = new AbortController();
      this.abort = controller;

      const scope = normalizeScope(this.client, this.scopeDefaults);
      const scopedClient = applyPodScope(this.client, scope.podId);

      const stream = await scopedClient.conversations.resumeStream(id, {
        pod_id: scope.podId ?? undefined,
        signal: controller.signal,
      });

      this.setConversationStatus("RUNNING");
      await this.consume({
        stream,
        controller,
        streamConversationId: id,
        syncAfterStream: resumeInput.syncOnTurnEnd,
      });
    } catch (resumeError) {
      const normalized = normalizeError(resumeError, "Failed to resume conversation.");
      this.patch({ error: normalized });
      this.options.onError?.(resumeError);
      throw normalized;
    }
  };

  resumeIfRunning = async (explicitConversationId?: string | null): Promise<boolean> => {
    const id = explicitConversationId ?? this.state.conversationId;
    if (!id) return false;
    if (this.state.isStreaming) return false;

    const statusKey = normalizeConversationStatus(this.state.status);
    const resumeKey = `${id}:${statusKey ?? "UNKNOWN"}`;
    if (this.autoResumedKey === resumeKey) {
      return false;
    }

    const knownRunning = isConversationRunningStatus(this.state.status);
    if (!knownRunning) {
      const latestConversation = await this.refreshConversation(id);
      if (!latestConversation || !isConversationRunningStatus(latestConversation.status)) {
        return false;
      }
    }

    const previousResumeKey = this.autoResumedKey;
    this.autoResumedKey = resumeKey;
    try {
      await this.resume({ conversationId: id, onlyIfRunning: true });
      return true;
    } catch (error) {
      if (this.autoResumedKey === resumeKey) {
        this.autoResumedKey = previousResumeKey;
      }
      throw error;
    }
  };

  stop = async (explicitConversationId?: string | null): Promise<void> => {
    this.patch({ error: null });
    try {
      const id = requireConversationId(explicitConversationId ?? this.state.conversationId);

      const scope = normalizeScope(this.client, this.scopeDefaults);
      const scopedClient = applyPodScope(this.client, scope.podId);

      await scopedClient.conversations.stopRun(id, { pod_id: scope.podId ?? undefined });
      this.setConversationStatus("WAITING");
      this.clearStreamingText();
    } catch (stopError) {
      const normalized = normalizeError(stopError, "Failed to stop conversation.");
      this.patch({ error: normalized });
      this.options.onError?.(stopError);
      throw normalized;
    }
  };
}
