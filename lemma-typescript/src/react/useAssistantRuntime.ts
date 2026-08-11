import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConversationMessage } from "../types.js";

type RuntimeConversationMessage = ConversationMessage & { conversation_id?: string };

export interface UseAssistantRuntimeOptions {
  conversationId?: string | null;
  sessionConversationId?: string | null;
  sessionMessages?: ConversationMessage[];
  /**
   * How many recently-opened conversations keep their messages in the store.
   * Re-opening one of these is instant and silent; anything older reloads.
   */
  retainConversations?: number;
}

export interface UseAssistantRuntimeResult {
  runtimeMessages: ConversationMessage[];
  appendOptimisticUserMessage: (
    content: string,
    options?: { conversationId?: string | null },
  ) => ConversationMessage;
  replaceLoadedMessages: (messages: ConversationMessage[]) => void;
  mergeMessages: (messages: ConversationMessage[]) => void;
  /** Whether this conversation's transcript is already in the store. */
  hasConversationMessages: (conversationId: string | null | undefined) => boolean;
  clear: () => void;
}

/**
 * Enough that moving between the conversations you are actually working across
 * never reloads, small enough that a long browsing session cannot accumulate
 * unbounded transcripts.
 */
const DEFAULT_RETAINED_CONVERSATIONS = 5;

function messageText(message: Pick<ConversationMessage, "text">): string {
  return typeof message.text === "string" ? message.text.trim() : "";
}

function messageTime(message: RuntimeConversationMessage): number {
  const timestamp = new Date(message.created_at).getTime();
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function isOptimisticId(messageId: string): boolean {
  return messageId.startsWith("optimistic-user-");
}

const OPTIMISTIC_MATCH_WINDOW_MS = 2 * 60 * 1000;

function upsertRuntimeMessage(
  previous: RuntimeConversationMessage[],
  incoming: RuntimeConversationMessage,
): RuntimeConversationMessage[] {
  const next = [...previous];
  const directIndex = next.findIndex((message) => message.id === incoming.id);

  if (directIndex >= 0) {
    next[directIndex] = incoming;
    return next;
  }

  if (incoming.role === "user") {
    const incomingText = messageText(incoming);
    if (incomingText) {
      const incomingTimestamp = messageTime(incoming);
      let optimisticIndex = -1;
      let bestDistance = Number.POSITIVE_INFINITY;

      next.forEach((message, index) => {
        if (
          message.role !== "user"
          || !isOptimisticId(message.id)
          || messageText(message) !== incomingText
        ) {
          return;
        }

        const distance = Math.abs(messageTime(message) - incomingTimestamp);
        if (distance > OPTIMISTIC_MATCH_WINDOW_MS || distance >= bestDistance) {
          return;
        }

        optimisticIndex = index;
        bestDistance = distance;
      });

      if (optimisticIndex >= 0) {
        next[optimisticIndex] = incoming;
        return next;
      }
    }
  }

  next.push(incoming);
  return next;
}

function toRuntimeMessage(
  message: ConversationMessage,
  fallbackConversationId?: string | null,
): RuntimeConversationMessage {
  const runtimeMessage = message as RuntimeConversationMessage;
  if (runtimeMessage.conversation_id || !fallbackConversationId) {
    return runtimeMessage;
  }

  return {
    ...runtimeMessage,
    conversation_id: fallbackConversationId,
  };
}

function buildOptimisticId(): string {
  if (typeof globalThis.crypto !== "undefined" && typeof globalThis.crypto.randomUUID === "function") {
    return `optimistic-user-${globalThis.crypto.randomUUID()}`;
  }

  return `optimistic-user-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useAssistantRuntime({
  conversationId = null,
  sessionConversationId = null,
  sessionMessages = [],
  retainConversations = DEFAULT_RETAINED_CONVERSATIONS,
}: UseAssistantRuntimeOptions): UseAssistantRuntimeResult {
  const [runtimeMessages, setRuntimeMessages] = useState<RuntimeConversationMessage[]>([]);
  // Mirrors the committed store so `hasConversationMessages` can answer from an
  // event handler without taking the list as a dependency.
  const runtimeMessagesRef = useRef<RuntimeConversationMessage[]>(runtimeMessages);
  runtimeMessagesRef.current = runtimeMessages;

  const mergeMessages = useCallback((messages: ConversationMessage[]) => {
    setRuntimeMessages((previous) => {
      const merged = messages.reduce(
        (accumulator, message) => upsertRuntimeMessage(accumulator, toRuntimeMessage(message, conversationId)),
        previous,
      );

      return [...merged].sort((a, b) => messageTime(a) - messageTime(b));
    });
  }, [conversationId]);

  const replaceLoadedMessages = useCallback((messages: ConversationMessage[]) => {
    const normalized = messages
      .map((message) => toRuntimeMessage(message, conversationId))
      .filter((message) => !conversationId || message.conversation_id === conversationId);

    setRuntimeMessages((previous) => {
      // Only this conversation is replaced. Other retained transcripts are held
      // aside and put back, so loading one conversation cannot evict the rest.
      const belongsToAnother = (message: RuntimeConversationMessage) => (
        !!message.conversation_id && !!conversationId && message.conversation_id !== conversationId
      );
      const otherConversations = previous.filter(belongsToAnother);
      const ownConversation = previous.filter((message) => !belongsToAnother(message));

      // Loads can complete after optimistic appends or stream events. Merge the
      // loaded snapshot into the current runtime state so newer local messages
      // are not temporarily dropped while the server catches up.
      const merged = normalized.reduce(
        (accumulator, message) => upsertRuntimeMessage(accumulator, message),
        ownConversation,
      );

      return [...otherConversations, ...merged].sort((a, b) => messageTime(a) - messageTime(b));
    });
  }, [conversationId]);

  const appendOptimisticUserMessage = useCallback((
    content: string,
    options?: { conversationId?: string | null },
  ): ConversationMessage => {
    const trimmed = content.trim();
    const optimisticConversationId = options?.conversationId ?? conversationId ?? undefined;
    const optimistic: RuntimeConversationMessage = {
      id: buildOptimisticId(),
      role: "user",
      kind: "TEXT",
      text: trimmed,
      created_at: new Date().toISOString(),
      metadata: null,
      ...(optimisticConversationId ? { conversation_id: optimisticConversationId } : {}),
    };

    setRuntimeMessages((previous) => {
      const next = upsertRuntimeMessage(previous, optimistic);
      return [...next].sort((a, b) => messageTime(a) - messageTime(b));
    });

    return optimistic;
  }, [conversationId]);

  const clear = useCallback(() => {
    recentConversationIdsRef.current = [];
    setRuntimeMessages([]);
  }, []);

  const hasConversationMessages = useCallback((targetConversationId: string | null | undefined) => {
    if (!targetConversationId) return false;
    return runtimeMessagesRef.current.some((message) => message.conversation_id === targetConversationId);
  }, []);

  // The store keeps the last few conversations rather than only the open one.
  // Dropping the previous transcript on every switch is what made re-opening a
  // conversation you were just in a full blank-and-refetch: the messages were
  // discarded a frame before the loader was asked for them again.
  useEffect(() => {
    lastSessionMessageIdRef.current = null;
    if (!conversationId) return;

    const recent = [
      conversationId,
      ...recentConversationIdsRef.current.filter((id) => id !== conversationId),
    ].slice(0, Math.max(1, retainConversations));
    recentConversationIdsRef.current = recent;

    const retained = new Set(recent);
    setRuntimeMessages((previous) => {
      const next = previous.filter((message) => (
        // A message with no conversation of its own is in-flight local state
        // (an optimistic user turn); it is scoped by the display filter, not here.
        !message.conversation_id || retained.has(message.conversation_id)
      ));
      return next.length === previous.length ? previous : next;
    });
  }, [conversationId, retainConversations]);

  const lastSessionMessageIdRef = useRef<string | null>(null);
  const recentConversationIdsRef = useRef<string[]>([]);

  useEffect(() => {
    if (sessionMessages.length === 0) return;

    const lastSessionMessage = sessionMessages[sessionMessages.length - 1];
    const lastSessionId = lastSessionMessage?.id ?? null;
    if (lastSessionId && lastSessionId === lastSessionMessageIdRef.current) return;
    lastSessionMessageIdRef.current = lastSessionId;

    const fallbackConversationId = sessionConversationId ?? conversationId;

    const normalized = sessionMessages
      .map((message) => toRuntimeMessage(message, fallbackConversationId))
      .filter((message) => !conversationId || message.conversation_id === conversationId);

    if (normalized.length === 0) return;
    mergeMessages(normalized);
  }, [conversationId, mergeMessages, sessionConversationId, sessionMessages]);

  // Session messages are mirrored into state by the effect above, which runs
  // *after* commit — so for one render a message that has already arrived is not
  // in the store yet. That gap is why the assistant's answer blinks out as a turn
  // ends: the session drops the streamed token buffer the moment the durable
  // message upserts, and the durable message is still one commit away.
  //
  // Merging the session's view in at derive time closes the gap instead of
  // papering over it downstream. Steady state costs nothing: once the effect has
  // mirrored a message, its id is present and the store is returned untouched.
  const mergedRuntimeMessages = useMemo(() => {
    if (sessionMessages.length === 0) return runtimeMessages;

    const present = new Set(runtimeMessages.map((message) => message.id));
    const missing = sessionMessages.filter((message) => !present.has(message.id));
    if (missing.length === 0) return runtimeMessages;

    const fallbackConversationId = sessionConversationId ?? conversationId;
    const merged = missing.reduce(
      (accumulator, message) => upsertRuntimeMessage(accumulator, toRuntimeMessage(message, fallbackConversationId)),
      runtimeMessages,
    );
    return [...merged].sort((a, b) => messageTime(a) - messageTime(b));
  }, [conversationId, runtimeMessages, sessionConversationId, sessionMessages]);

  return {
    runtimeMessages: mergedRuntimeMessages,
    appendOptimisticUserMessage,
    replaceLoadedMessages,
    mergeMessages,
    hasConversationMessages,
    clear,
  };
}
