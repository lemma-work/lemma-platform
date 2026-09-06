import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
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
  isConversationRunningStatus,
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
  /** The conversation was renamed mid-stream by the server's title generator. */
  onTitle?: (title: string, conversationId: string | null) => void;
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
  /**
   * The conversation record the caller is already holding. Supplying it is what
   * keeps a send from re-reading a conversation the caller just opened or
   * created — the session's own copy is a render behind at that point.
   */
  knownConversation?: Conversation | null;
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
  streamingThinking: string;
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
  retryFailedRun: (conversationId?: string | null) => Promise<Conversation>;
  resume: (conversationId?: string | null | ResumeAssistantOptions) => Promise<void>;
  resumeIfRunning: (
    conversationId?: string | null,
    options?: {
      knownConversation?: Conversation | null;
      /**
       * The caller just did something that starts a run. Keeps asking for a
       * few seconds rather than concluding from one read that nothing is
       * running — a resume handed to a worker has not started yet.
       *
       * `"queued"` is the same statement with the server's own word for it:
       * the decision committed and the work that follows it is a job. That
       * job runs the approved tool *before* the run starts, and an approved
       * tool is allowed to take minutes, so the wait is measured against the
       * tool rather than against the queue.
       */
      expectRun?: boolean | "queued";
      /**
       * Bypass the dedup key that skips a resume already "consumed" for this
       * conversation+status pair. Needed right after an explicit user action
       * (e.g. approving a paused permission request) that is known to warrant
       * a fresh reconnect even when status hasn't changed since the last
       * resume — an Agent Host permission wait never leaves RUNNING, so the
       * ordinary key would otherwise look identical to one already used by a
       * subscription that has since died.
       */
      force?: boolean;
    },
  ) => Promise<boolean>;
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

/** Did a re-read of the conversation actually bring anything back?
 *
 * The record is small, flat and server-shaped, so its serialization is stable
 * enough to compare whole — and comparing whole is what keeps this honest as
 * fields are added. Naming the handful the UI reads today would quietly stop
 * noticing the next one.
 */
function sameConversationRecord(left: Conversation, right: Conversation): boolean {
  if (left.id !== right.id) return false;
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

/**
 * How long to keep asking whether the run somebody just triggered has started.
 *
 * Answering an `ask_user` card resolves inline, but an approved
 * `request_approval` hands its reconciliation to a worker and returns
 * `"queued"` — so the conversation is still WAITING for the moment it takes
 * that job to be picked up. One read landed inside that window, concluded
 * nothing was running, and left the transcript still until a reload.
 */
const RESUME_RACE_DELAYS_MS = [400, 900, 2000];

/**
 * The same question, asked for as long as the answer can honestly still be
 * "not yet".
 *
 * An approved `request_approval` is answered `"queued"`, and the job that picks
 * it up runs the approved tool *first* — the conversation only reads RUNNING
 * once that tool has finished. Deferring it to a worker is precisely an
 * admission that it may take minutes, so a ladder that ran out after three
 * seconds was timing the queue when the thing it had to outlast was the tool.
 * It gave up, nothing else was watching, and the answer landed in a transcript
 * with no listener until the page was reloaded.
 *
 * Long, and cheap to be long: ten conversation reads spread over ~2 minutes,
 * every one of which stops the moment a stream attaches.
 */
const QUEUED_RESUME_DELAYS_MS = [400, 900, 2000, 4000, 8000, 15_000, 30_000, 30_000, 30_000];

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Wait out the backoff before the next reconnect attempt — or until somebody
 * asks us to stop waiting.
 *
 * The waking is what lets a user action feel like one. This backoff climbs to
 * ten seconds and is otherwise blind to the person on the other side, so
 * approving a permission request mid-backoff bought nothing: the forced resume
 * meant to cover exactly that case sees `isStreaming` still true — this loop
 * lives inside `consume` — and declines. So the resume wakes the sleep rather
 * than trying to duplicate it. The attempt counter is deliberately left alone:
 * one click buys one early attempt, not a reset of the backoff.
 */
function waitForStreamReconnect(
  attempt: number,
  signal: AbortSignal,
  wakeRef: RefObject<(() => void) | null>,
): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);

  const delayMs = Math.min(2 ** Math.min(Math.max(attempt - 1, 0), 4) * 1000, 10_000);
  return new Promise((resolve) => {
    let timeoutId: ReturnType<typeof setTimeout>;
    const settle = (shouldReconnect: boolean) => {
      clearTimeout(timeoutId);
      signal.removeEventListener("abort", handleAbort);
      if (wakeRef.current === wake) wakeRef.current = null;
      resolve(shouldReconnect);
    };
    const handleAbort = () => settle(false);
    const wake = () => settle(true);

    timeoutId = setTimeout(wake, delayMs);
    signal.addEventListener("abort", handleAbort, { once: true });
    wakeRef.current = wake;
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
    onTitle,
    onError,
  } = options;

  const [conversationId, setConversationIdState] = useState<string | null>(externalConversationId);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [streamingThinking, setStreamingThinking] = useState("");
  const [streamingTool, setStreamingTool] = useState<AssistantStreamingTool | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  // Shadows `conversation` so a send that follows a create in the same tick can
  // see it. React state is a render behind by design, and the controller drives
  // create-then-send without ever going back through a render — which is how
  // sending the first message used to re-fetch the conversation the create call
  // had just handed back.
  const conversationRecordRef = useRef<Conversation | null>(null);
  const conversationIdRef = useRef<string | null>(externalConversationId);
  const statusRef = useRef<string | undefined>(undefined);
  const streamingTextRef = useRef("");
  const streamingThinkingRef = useRef("");
  const streamingToolTokenRef = useRef("");
  const autoResumedKeyRef = useRef<string | null>(null);
  const autoLoadInFlightKeyRef = useRef<string | null>(null);
  const lastAutoLoadedKeyRef = useRef<string | null>(null);
  const onEventRef = useRef(onEvent);
  const onStatusRef = useRef(onStatus);
  const onMessageRef = useRef(onMessage);
  const onTitleRef = useRef(onTitle);
  const onErrorRef = useRef(onError);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const consumeRef = useRef<(opts: any) => Promise<void>>(null!);
  const streamReconnectCountRef = useRef(0);
  // Set while `consume` is sleeping between reconnect attempts; calling it
  // ends that sleep early. Null whenever nobody is waiting.
  const streamReconnectWakeRef = useRef<(() => void) | null>(null);

  // The only way the conversation record is written, so the ref above can never
  // drift from the state it shadows.
  //
  // A re-read that comes back identical keeps the identity already on screen.
  // Everything downstream of the record — the header, the retry affordance, the
  // composer's disabled state — re-renders on its identity, and now that a
  // record is re-read whenever a turn's ending is in doubt, most of those reads
  // return exactly what was already held.
  const rememberConversation = useCallback((next: Conversation | null) => {
    const previous = conversationRecordRef.current;
    if (previous === next) return;
    if (previous && next && sameConversationRecord(previous, next)) return;
    conversationRecordRef.current = next;
    setConversation(next);
  }, []);

  const setConversationId = useCallback((nextConversationId: string | null) => {
    // Only a genuine switch cancels an in-flight stream.
    //
    // This used to abort unconditionally, above the check — so the effect that
    // mirrors an externally-owned conversation id would cancel the stream a
    // send had *just* opened, in the case where the id had only moved from "no
    // conversation" to the one being streamed into. Nothing caught it for as
    // long as the send happened to do a spare network round-trip first: that
    // delay was what let React commit before the request installed its
    // controller, so the abort landed on nothing.
    //
    // Compared against the ref rather than the state, because `createConversation`
    // moves the id within the tick it is called in, and state is a render behind.
    if (conversationIdRef.current === nextConversationId) return;
    conversationIdRef.current = nextConversationId;
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
      streamingThinkingRef.current = "";
      setStreamingText("");
      setStreamingThinking("");
      setStreamingTool(null);
      conversationRecordRef.current = null;
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
    onTitleRef.current = onTitle;
  }, [onTitle]);

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
  const pendingThinkingFlushRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearStreamingText = useCallback(() => {
    if (pendingStreamingFlushRef.current) {
      clearTimeout(pendingStreamingFlushRef.current);
      pendingStreamingFlushRef.current = null;
    }
    streamingTextRef.current = "";
    setStreamingText("");
  }, []);

  const clearStreamingThinking = useCallback(() => {
    if (pendingThinkingFlushRef.current) {
      clearTimeout(pendingThinkingFlushRef.current);
      pendingThinkingFlushRef.current = null;
    }
    streamingThinkingRef.current = "";
    setStreamingThinking("");
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

  const appendStreamingThinking = useCallback((token: string) => {
    if (!token) return;
    streamingThinkingRef.current += token;
    if (!pendingThinkingFlushRef.current) {
      pendingThinkingFlushRef.current = setTimeout(() => {
        pendingThinkingFlushRef.current = null;
        setStreamingThinking(streamingThinkingRef.current);
      }, 0);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (pendingStreamingFlushRef.current) {
        clearTimeout(pendingStreamingFlushRef.current);
      }
      if (pendingThinkingFlushRef.current) {
        clearTimeout(pendingThinkingFlushRef.current);
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
        // Kept in step with the state, so the effect mirroring the external id
        // recognises this conversation as the one already open rather than as a
        // switch away from it.
        conversationIdRef.current = created.id;
        setConversationIdState(created.id);
        rememberConversation(created);
        setConversationStatus(created.status ?? undefined);
        setMessages([]);
        clearStreamingText();
        clearStreamingThinking();
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
    clearStreamingThinking,
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

      rememberConversation(nextConversation);
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
    agentRunId,
    syncAfterStream,
  }: {
    stream: ReadableStream<Uint8Array>;
    controller: AbortController;
    streamConversationId?: string | null;
    agentRunId?: string | null;
    syncAfterStream?: boolean;
  }): Promise<void> => {
    setIsStreaming(true);
    setError(null);
    clearStreamingText();
    clearStreamingThinking();
    let sawTerminalStatus = false;
    // A clean finish needs nothing from the server that the stream did not
    // already say. A failed one does: `last_run_error` and `last_run_retryable`
    // live on the conversation record, and they are what decides whether the
    // transcript offers a Retry.
    let sawFailedStatus = false;
    // Set where the buffer is cleared, read where the turn is reconciled.
    let unclaimedAnswer = false;
    let streamFailure: unknown = null;

    try {
      for await (const event of readSSE(stream)) {
        if (controller.signal.aborted) {
          break;
        }

        const payload = parseSSEJson(event);
        onEventRef.current?.(event, payload);

        const parsed = parseAssistantStreamEvent(payload);
        if (parsed.interrupted) {
          // The transport died, not the run. Deliberately no error state and
          // no terminal flag: falling out of this loop with `sawTerminalStatus`
          // still false is what puts us on the catch-up-and-reconnect path
          // below, which is what the server asked for by sending this.
          continue;
        }
        if (parsed.error) {
          const streamError = new Error(parsed.error);
          setError(streamError);
          onErrorRef.current?.(streamError);
          setConversationStatus(parsed.status ?? "FAILED");
          sawTerminalStatus = true;
          sawFailedStatus = true;
          clearStreamingText();
          clearStreamingThinking();
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
          } else if (parsed.tokenKind === "thinking") {
            appendStreamingThinking(parsed.token);
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
            clearStreamingThinking();
            clearStreamingTool();
          }
        }
        if (parsed.title) {
          // Conversation-scoped, not run-scoped: the title is generated from
          // the first user message while the run is still going, so it lands
          // mid-turn and says nothing about the run's status.
          const renamedConversationId = parsed.conversationId
            ?? streamConversationId
            ?? conversationIdRef.current;
          const renamed = conversationRecordRef.current;
          if (renamed && (!renamedConversationId || renamed.id === renamedConversationId)) {
            rememberConversation({ ...renamed, title: parsed.title });
          }
          onTitleRef.current?.(parsed.title, renamedConversationId ?? null);
        }
        if (parsed.status) {
          setConversationStatus(parsed.status);
          if (!isConversationRunningStatus(parsed.status)) {
            sawTerminalStatus = true;
            if (parsed.status === "FAILED") sawFailedStatus = true;
            // Read before the clear on the next line, which is the only place
            // this fact survives to: an answer still sitting in the token
            // buffer when the run ends is one whose durable message never
            // arrived.
            unclaimedAnswer = streamingTextRef.current.trim().length > 0;
            clearStreamingText();
            clearStreamingThinking();
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
              streamReconnectWakeRef,
            );
            if (!shouldReconnect) break;

            try {
              const scope = normalizeScope(client, defaultScope);
              const scopedClient = applyPodScope(client, scope.podId);
              const newStream = await scopedClient.conversations.resumeStream(syncConversationId, {
                pod_id: scope.podId ?? undefined,
                signal: controller.signal,
                agent_run_id: agentRunId,
              });
              streamReconnectCountRef.current = 0;
              return await consumeRef.current({
                stream: newStream,
                controller,
                streamConversationId: syncConversationId,
                agentRunId,
                syncAfterStream,
              });
            } catch (reconnectError) {
              if (reconnectError instanceof Error && reconnectError.name === "AbortError") break;
              streamFailure = reconnectError;
            }
          }
        } else if (syncConversationId) {
          // A stream that ran to a terminal status delivered every durable
          // message on its way there, so re-listing the transcript here asked
          // the server to repeat itself once per turn — and the runtime store
          // discarded the answer anyway, because it dedups on the last message
          // id and that id had not moved. Only an explicit opt-in re-lists now.
          const shouldReloadMessages = syncAfterStream ?? syncOnTurnEnd;
          // ...with one exception, and it is not an opt-in. Text streamed as
          // tokens that no durable message ever claimed is an answer that
          // exists only in a buffer this function is about to clear: every
          // assistant or tool message clears the buffer as it lands, so
          // anything left in it at the end is a frame that never arrived.
          // Publishing is best-effort by design — it swallows its own failures
          // and the fan-out drops a subscriber that falls behind — so "the
          // stream said everything" is a description of the happy path, not a
          // guarantee. One list, only when we can see something is missing.
          const answerWentMissing = !sawFailedStatus
            && (unclaimedAnswer || streamingTextRef.current.trim().length > 0);
          // The record is still worth re-reading after a failure: the retry
          // affordance is driven by `last_run_retryable`, which only the
          // conversation carries.
          if (shouldReloadMessages || sawFailedStatus || streamFailure) {
            await refreshConversation(syncConversationId);
          }
          if (shouldReloadMessages || answerWentMissing) {
            await loadMessages({ conversationId: syncConversationId, limit: 100 });
          }
        }

        if (!controller.signal.aborted && streamFailure) {
          const normalized = normalizeError(streamFailure, "Failed to stream conversation.");
          setError(normalized);
          onErrorRef.current?.(streamFailure);
        }
      }
    } finally {
      clearStreamingText();
      clearStreamingThinking();
      clearStreamingTool();
      if (controller.signal.aborted) streamReconnectCountRef.current = 0;
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      setIsStreaming(false);
    }
  }, [
    appendStreamingThinking,
    appendStreamingToken,
    clearStreamingThinking,
    clearStreamingTool,
    clearStreamingText,
    client,
    conversationId,
    defaultScope,
    loadMessages,
    refreshConversation,
    rememberConversation,
    setConversationStatus,
    syncOnTurnEnd,
  ]);

  useEffect(() => {
    consumeRef.current = consume;
  }, [consume]);

  const ensureConversation = useCallback(async (
    overrideConversationId?: string | null,
    knownConversation?: Conversation | null,
  ): Promise<Conversation> => {
    const existingId = overrideConversationId ?? conversationId;
    if (existingId) {
      // Three ways to already know this record, cheapest first. The caller's
      // copy comes from a controller that opened the conversation and is still
      // holding it; the ref is our own, and unlike the state it shadows it is
      // current within the tick a create happened in.
      if (knownConversation?.id === existingId) {
        rememberConversation(knownConversation);
        return knownConversation;
      }
      if (conversationRecordRef.current?.id === existingId) {
        return conversationRecordRef.current;
      }
      if (conversation?.id === existingId) {
        return conversation;
      }

      const existing = await refreshConversation(existingId);
      if (existing) return existing;
      throw new Error("Failed to resolve existing conversation.");
    }

    throw new Error("conversationId is required. Create a conversation before sending a message.");
  }, [conversation, conversationId, refreshConversation, rememberConversation]);

  const sendMessage = useCallback(async (
    content: string,
    input: SendAssistantMessageOptions = {},
  ): Promise<Conversation> => {
    setError(null);
    try {
      const resolvedConversation = await ensureConversation(
        input.conversationId,
        input.knownConversation,
      );
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

  const retryFailedRun = useCallback(async (
    explicitConversationId?: string | null,
  ): Promise<Conversation> => {
    setError(null);
    try {
      const resolvedConversation = await ensureConversation(explicitConversationId);
      const resolvedConversationId = requireConversationId(resolvedConversation.id);

      cancel();
      const controller = new AbortController();
      abortRef.current = controller;

      const scope = normalizeScope(client, defaultScope);
      const scopedClient = applyPodScope(client, scope.podId);
      const start = await scopedClient.conversations.retryFailedRun(
        resolvedConversationId,
        {
          pod_id: scope.podId ?? undefined,
          signal: controller.signal,
        },
      );

      setConversationStatus("RUNNING");
      let stream: ReadableStream<Uint8Array>;
      try {
        stream = await scopedClient.conversations.resumeStream(
          resolvedConversationId,
          {
            pod_id: scope.podId ?? undefined,
            signal: controller.signal,
            agent_run_id: start.agent_run_id,
          },
        );
      } catch (attachError) {
        const latestConversation = await refreshConversation(resolvedConversationId);
        if (!latestConversation) throw attachError;
        stream = await scopedClient.conversations.resumeStream(
          resolvedConversationId,
          {
            pod_id: scope.podId ?? undefined,
            signal: controller.signal,
            agent_run_id: start.agent_run_id,
          },
        );
      }
      await consume({
        stream,
        controller,
        streamConversationId: resolvedConversationId,
        agentRunId: start.agent_run_id,
        syncAfterStream: syncOnTurnEnd,
      });
      return resolvedConversation;
    } catch (retryError) {
      const normalized = normalizeError(retryError, "Failed to retry agent message.");
      setError(normalized);
      onErrorRef.current?.(retryError);
      throw normalized;
    }
  }, [cancel, client, consume, defaultScope, ensureConversation, refreshConversation, setConversationStatus, syncOnTurnEnd]);

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

  const resumeIfRunning = useCallback(async (
    explicitConversationId?: string | null,
    options?: {
      knownConversation?: Conversation | null;
      expectRun?: boolean | "queued";
      force?: boolean;
    },
  ): Promise<boolean> => {
    const id = explicitConversationId ?? conversationId;
    if (!id) return false;
    if (isStreaming) {
      // `isStreaming` covers the reconnect loop inside `consume` as well as an
      // actually-live stream, and those want opposite things from a forced
      // resume. A live stream needs to be left alone — it is already carrying
      // the run. A loop asleep in its backoff is not carrying anything, and
      // the user has just done the thing worth waking up for. Either way this
      // does not open a second subscription: waking is all it can do.
      if (options?.force) streamReconnectWakeRef.current?.();
      return false;
    }

    // A caller that has just read the conversation can hand it over rather than
    // make us read it again: without a hint the only way to answer "is this
    // running?" is to go and fetch the record, which is what the caller has in
    // its hand. Trusted only for the conversation it actually describes.
    const knownConversation = options?.knownConversation?.id === id
      ? options.knownConversation
      : null;
    const statusKey = normalizeConversationStatus(knownConversation?.status ?? statusRef.current);
    const resumeKey = `${id}:${statusKey ?? "UNKNOWN"}`;
    if (options?.force) {
      autoResumedKeyRef.current = null;
    }
    if (autoResumedKeyRef.current === resumeKey) {
      return false;
    }

    if (knownConversation) {
      if (!isConversationRunningStatus(knownConversation.status)) return false;
      rememberConversation(knownConversation);
      setConversationStatus(statusKey);
    } else if (!isConversationRunningStatus(statusRef.current)) {
      // `expectRun` is a caller saying it just did the thing that starts a run.
      // The read then has to survive the gap between "the server accepted it"
      // and "the run exists": an approved `request_approval` commits its
      // decision and hands the resume to a worker, so the record still reads
      // WAITING for as long as that job waits to be picked up. Without the
      // ladder the single read landed in that gap, answered "nothing is
      // running", and nothing ever asked again — the follow-on run then wrote a
      // whole answer into a conversation with no listener.
      //
      // Only for a caller that expects one. Everywhere else this stays one
      // read, because everywhere else a not-running conversation is just a
      // conversation that is not running.
      const delays = options?.expectRun === "queued"
        ? QUEUED_RESUME_DELAYS_MS
        : RESUME_RACE_DELAYS_MS;
      const attempts = options?.expectRun ? delays.length + 1 : 1;
      let started = false;
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        if (attempt > 0) await delay(delays[attempt - 1]);
        // Someone else got there first (a send, or a stream that attached while
        // this was waiting). The ref, not the state: this is inside an await.
        if (abortRef.current !== null) return false;
        // The queued ladder runs for minutes, which is long enough for the
        // reader to have moved on. `refreshConversation` writes the session's
        // status from whatever it reads, so one that outlived its conversation
        // would stamp this session with the status of a conversation nobody is
        // looking at any more.
        if (conversationIdRef.current !== id) return false;
        const latestConversation = await refreshConversation(id);
        if (latestConversation && isConversationRunningStatus(latestConversation.status)) {
          started = true;
          break;
        }
      }
      if (!started) {
        if (options?.expectRun === "queued" && conversationIdRef.current === id) {
          // Every rung read WAITING, and there are two ways to arrive there:
          // the job still has not started the run, or it started and finished
          // one entirely between two rungs. The second leaves a transcript
          // holding both the tool return and the whole answer, with nothing
          // that will ever mention them again. One read, once, so that case
          // costs a stale card until the ladder ends rather than until the
          // page is reloaded.
          await loadMessages({ conversationId: id, limit: 100 }).catch(() => undefined);
        }
        return false;
      }
      if (options?.expectRun === "queued") {
        // The gap this ladder just waited out is a gap in which the server was
        // writing. A queued decision appends the approved call's tool return
        // and only then starts the run we are about to attach to, so that
        // return was published while nothing was subscribed — and the stream we
        // are opening carries the new run, not the rows that preceded it. One
        // read, at the only moment we know there is something to catch up on.
        await loadMessages({ conversationId: id, limit: 100 }).catch(() => undefined);
        if (conversationIdRef.current !== id || abortRef.current !== null) return false;
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
  }, [conversationId, isStreaming, loadMessages, refreshConversation, resume, setConversationStatus]);

  const stop = useCallback(async (explicitConversationId?: string | null): Promise<void> => {
    setError(null);
    try {
      const id = requireConversationId(explicitConversationId ?? conversationId);

      const scope = normalizeScope(client, defaultScope);
      const scopedClient = applyPodScope(client, scope.podId);

      const stopped = await scopedClient.conversations.stopRun(id, {
        pod_id: scope.podId ?? undefined,
      });
      setConversationStatus(normalizeConversationStatus(stopped.status) ?? "STOP_REQUESTED");
    } catch (stopError) {
      const normalized = normalizeError(stopError, "Failed to stop conversation.");
      setError(normalized);
      onErrorRef.current?.(stopError);
      throw normalized;
    }
  }, [client, conversationId, defaultScope, setConversationStatus]);

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
    streamingThinking,
    streamingTool,
    isStreaming,
    error,
    setConversationId,
    listConversations,
    createConversation,
    refreshConversation,
    loadMessages,
    sendMessage,
    retryFailedRun,
    resume,
    resumeIfRunning,
    stop,
    cancel,
    clearMessages,
  };
}
