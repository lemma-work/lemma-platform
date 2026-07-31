import { useCallback, useEffect, useMemo, useState } from "react";
import type { LemmaClient } from "../client.js";
import type { Conversation } from "../types.js";
import {
  useAssistantSession,
  type CreateConversationInput,
} from "./useAssistantSession.js";
import { normalizeError } from "./utils.js";

export interface UseConversationsOptions {
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
  enabled?: boolean;
  autoLoad?: boolean;
  limit?: number;
  pageToken?: string;
  initialConversationId?: string | null;
}

export interface UseConversationsResult {
  conversations: Conversation[];
  total: number;
  nextPageToken: string | null;
  /**
   * @deprecated Use selectedConversationId instead.
   */
  activeConversationId: string | null;
  /**
   * @deprecated Use selectedConversation instead.
   */
  activeConversation: Conversation | null;
  selectedConversationId: string | null;
  effectiveSelectedConversationId: string | null;
  selectedConversation: Conversation | null;
  isLoading: boolean;
  isLoadingMore: boolean;
  error: Error | null;
  selectConversation: (conversationId: string | null) => void;
  clearSelection: () => void;
  selectLatestConversation: () => string | null;
  refresh: (overrides?: { limit?: number; pageToken?: string }) => Promise<Conversation[]>;
  loadMore: (overrides?: { limit?: number }) => Promise<Conversation[]>;
  createConversation: (input?: CreateConversationInput) => Promise<Conversation>;
  createAndSelectConversation: (input?: Omit<CreateConversationInput, "setActive">) => Promise<Conversation>;
  ensureConversation: (input?: Omit<CreateConversationInput, "setActive">) => Promise<Conversation>;
}

function sortConversationsByUpdatedAt(conversations: Conversation[]): Conversation[] {
  return [...conversations].sort((a, b) => {
    const aTime = new Date(a.updated_at || a.created_at).getTime();
    const bTime = new Date(b.updated_at || b.created_at).getTime();
    return bTime - aTime;
  });
}

export function useConversations({
  client,
  podId,
  agentName,
  assistantName,
  assistantId,
  organizationId,
  enabled = true,
  autoLoad = true,
  limit = 20,
  pageToken,
  initialConversationId = null,
}: UseConversationsOptions): UseConversationsResult {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [total, setTotal] = useState(0);
  const [nextPageToken, setNextPageToken] = useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(initialConversationId);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  const scopeKey = useMemo(() => JSON.stringify({
    podId: podId ?? null,
    agentName: agentName ?? assistantName ?? assistantId ?? null,
    assistantName: assistantName ?? null,
    assistantId: assistantId ?? null,
    organizationId: organizationId ?? null,
  }), [agentName, assistantId, assistantName, organizationId, podId]);

  const {
    error,
    listConversations,
    createConversation: sessionCreateConversation,
    refreshConversation: sessionRefreshConversation,
  } = useAssistantSession({
    client,
    podId,
    agentName: agentName ?? assistantName ?? assistantId,
    assistantName,
    assistantId,
    organizationId,
    autoLoad: false,
  });

  const refresh = useCallback(async (
    overrides: { limit?: number; pageToken?: string } = {},
  ): Promise<Conversation[]> => {
    if (!enabled) {
      setConversations([]);
      setTotal(0);
      setNextPageToken(null);
      setIsLoading(false);
      return [];
    }

    setIsLoading(true);
    try {
      const response = await listConversations({
        limit: overrides.limit ?? limit,
        pageToken: overrides.pageToken ?? pageToken,
      });
      const nextConversations = sortConversationsByUpdatedAt(response.items ?? []);
      setConversations(nextConversations);
      setTotal(response.total ?? nextConversations.length);
      setNextPageToken(response.next_page_token ?? null);
      return nextConversations;
    } finally {
      setIsLoading(false);
    }
  }, [enabled, limit, listConversations, pageToken]);

  const loadMore = useCallback(async (overrides: { limit?: number } = {}): Promise<Conversation[]> => {
    if (!enabled || !nextPageToken || isLoading || isLoadingMore) {
      return [];
    }

    setIsLoadingMore(true);
    try {
      const response = await listConversations({
        limit: overrides.limit ?? limit,
        pageToken: nextPageToken,
      });
      const moreConversations = sortConversationsByUpdatedAt(response.items ?? []);
      setConversations((previous) => [...previous, ...moreConversations]);
      setTotal(response.total ?? conversations.length + moreConversations.length);
      setNextPageToken(response.next_page_token ?? null);
      return moreConversations;
    } catch (loadError) {
      const normalized = normalizeError(loadError, "Failed to load more conversations.");
      throw normalized;
    } finally {
      setIsLoadingMore(false);
    }
  }, [conversations.length, enabled, isLoading, isLoadingMore, limit, listConversations, nextPageToken]);

  const createConversation = useCallback(async (input: CreateConversationInput = {}): Promise<Conversation> => {
    const createdConversation = await sessionCreateConversation({
      ...input,
      podId: input.podId ?? podId ?? undefined,
      agentName: input.agentName ?? input.assistantName ?? agentName ?? assistantName ?? assistantId ?? undefined,
      organizationId: input.organizationId ?? organizationId ?? undefined,
      setActive: input.setActive ?? true,
    });

    setConversations((previous) => {
      const nextConversations = sortConversationsByUpdatedAt([
        createdConversation,
        ...previous.filter((conversation) => conversation.id !== createdConversation.id),
      ]);
      setTotal(nextConversations.length);
      return nextConversations;
    });

    if (input.setActive !== false) {
      setSelectedConversationId(createdConversation.id);
    }

    return createdConversation;
  }, [agentName, assistantId, assistantName, organizationId, podId, sessionCreateConversation]);

  const createAndSelectConversation = useCallback(async (
    input: Omit<CreateConversationInput, "setActive"> = {},
  ): Promise<Conversation> => {
    return createConversation({
      ...input,
      setActive: true,
    });
  }, [createConversation]);

  const selectConversation = useCallback((conversationId: string | null) => {
    setSelectedConversationId(conversationId);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedConversationId(null);
  }, []);

  const selectLatestConversation = useCallback((): string | null => {
    const latestConversationId = conversations[0]?.id ?? null;
    setSelectedConversationId(latestConversationId);
    return latestConversationId;
  }, [conversations]);

  useEffect(() => {
    setSelectedConversationId(initialConversationId);
  }, [initialConversationId]);

  useEffect(() => {
    if (!enabled) {
      setConversations([]);
      setTotal(0);
      setNextPageToken(null);
      setSelectedConversationId(initialConversationId);
      setIsLoading(false);
      setIsLoadingMore(false);
      return;
    }

    setConversations([]);
    setTotal(0);
    setNextPageToken(null);
    setSelectedConversationId(initialConversationId);

    if (!autoLoad) return;
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        await refresh();
      } catch {
        if (!cancelled) {
          // refresh handles errors internally
        }
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [autoLoad, enabled, initialConversationId, refresh, scopeKey]);

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );

  const ensureConversation = useCallback(async (
    input: Omit<CreateConversationInput, "setActive"> = {},
  ): Promise<Conversation> => {
    const existingConversation = selectedConversation;
    if (existingConversation) {
      return existingConversation;
    }

    if (selectedConversationId) {
      const fetchedConversation = await sessionRefreshConversation(selectedConversationId);
      if (!fetchedConversation) {
        throw new Error("Failed to resolve selected conversation.");
      }
      setConversations((previous) => sortConversationsByUpdatedAt([
        fetchedConversation,
        ...previous.filter((conversation) => conversation.id !== fetchedConversation.id),
      ]));
      return fetchedConversation;
    }

    return createAndSelectConversation(input);
  }, [
    createAndSelectConversation,
    selectedConversation,
    selectedConversationId,
    sessionRefreshConversation,
  ]);

  return useMemo(() => ({
    conversations,
    total,
    nextPageToken,
    activeConversationId: selectedConversationId,
    activeConversation: selectedConversation,
    selectedConversationId,
    effectiveSelectedConversationId: selectedConversationId,
    selectedConversation,
    isLoading,
    isLoadingMore,
    error,
    selectConversation,
    clearSelection,
    selectLatestConversation,
    refresh,
    loadMore,
    createConversation,
    createAndSelectConversation,
    ensureConversation,
  }), [
    clearSelection,
    conversations,
    createAndSelectConversation,
    createConversation,
    error,
    isLoading,
    isLoadingMore,
    loadMore,
    nextPageToken,
    refresh,
    ensureConversation,
    selectConversation,
    selectLatestConversation,
    selectedConversation,
    selectedConversationId,
    total,
  ]);
}
