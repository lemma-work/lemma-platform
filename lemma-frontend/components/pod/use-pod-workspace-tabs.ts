'use client';

import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { useQueries } from '@tanstack/react-query';

import {
    HOME_WORKSPACE_TAB,
    NEW_WORKSPACE_TAB,
    appWorkspaceTab,
    closeWorkspaceTab,
    conversationWorkspaceTab,
    formatWorkspaceAppTitle,
    getAppSlugFromWorkspaceTab,
    getActiveWorkspaceTabId,
    getPodWorkspaceTabsStorageKey,
    parseWorkspaceTabs,
    promoteNewConversationTab,
    workspaceTabConversationQueryKey,
    routeWorkspaceTab,
    serializeWorkspaceTabs,
    syncAppWorkspaceTabs,
    syncWorkspaceTabMetadata,
    upsertWorkspaceTab,
    type PodWorkspaceTab,
} from '@/lib/pods/workspace-tabs';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { getConversationStatusView } from '@/lib/utils/conversations';
import type { Conversation } from '@/lib/types';
import type { AppPageRef } from '@/lib/types/app';

interface UsePodWorkspaceTabsOptions {
    enabled?: boolean;
    podId: string;
    pathname: string;
    currentHref: string;
    routeTitle: string;
    appSlug: string | null;
    /** Used only to give the ephemeral focused-app tab its real title/icon —
     *  never to pin anything. */
    pages: AppPageRef[];
    appsLoaded: boolean;
    conversations: Conversation[];
    openedConversationId: string | null;
}

interface PodWorkspaceTabsStore {
    tabs: PodWorkspaceTab[];
    listeners: Set<() => void>;
}

const SERVER_TABS: PodWorkspaceTab[] = [HOME_WORKSPACE_TAB];
const podWorkspaceTabStores = new Map<string, PodWorkspaceTabsStore>();
const UNLISTED_CONVERSATION_POLL_MS = 4000;

function isUnsettledConversation(value: unknown) {
    const status = (value as { status?: unknown } | undefined)?.status;
    const view = getConversationStatusView(status);
    return view.isActive || view.isAwaiting;
}

function getPodWorkspaceTabsStore(podId: string) {
    const existing = podWorkspaceTabStores.get(podId);
    if (existing) return existing;

    let tabs = SERVER_TABS;
    if (typeof window !== 'undefined') {
        try {
            tabs = parseWorkspaceTabs(
                window.localStorage.getItem(getPodWorkspaceTabsStorageKey(podId)),
            );
        } catch {
            tabs = SERVER_TABS;
        }
    }

    const store: PodWorkspaceTabsStore = { tabs, listeners: new Set() };
    podWorkspaceTabStores.set(podId, store);
    return store;
}

function persistPodWorkspaceTabsStore(podId: string, store: PodWorkspaceTabsStore) {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(
            getPodWorkspaceTabsStorageKey(podId),
            serializeWorkspaceTabs(store.tabs),
        );
    } catch {
        // The in-memory workspace remains usable when storage is unavailable.
    }
}

function updatePodWorkspaceTabs(
    podId: string,
    update: (tabs: PodWorkspaceTab[]) => PodWorkspaceTab[],
) {
    const store = getPodWorkspaceTabsStore(podId);
    const next = update(store.tabs);
    if (next === store.tabs) return;

    store.tabs = next;
    persistPodWorkspaceTabsStore(podId, store);
    store.listeners.forEach((listener) => listener());
}

export function usePodWorkspaceTabs({
    enabled = true,
    podId,
    pathname,
    currentHref,
    routeTitle,
    appSlug,
    pages,
    appsLoaded,
    conversations,
    openedConversationId,
}: UsePodWorkspaceTabsOptions) {
    const store = getPodWorkspaceTabsStore(podId);
    const subscribe = useCallback((listener: () => void) => {
        store.listeners.add(listener);
        return () => store.listeners.delete(listener);
    }, [store]);
    const getSnapshot = useCallback(() => store.tabs, [store]);
    const tabs = useSyncExternalStore(subscribe, getSnapshot, () => SERVER_TABS);
    // A presented widget is identified by its tool call, which lives in the
    // query rather than the path, so the active-tab lookup needs both halves.
    const currentSearchParams = useMemo(
        () => new URLSearchParams(currentHref.split('?')[1] || ''),
        [currentHref],
    );
    const activeTabId = getActiveWorkspaceTabId(podId, pathname, appSlug, currentSearchParams);
    const wasNewConversationRouteRef = useRef(false);
    const newConversationBaselineRef = useRef<string | null>(null);
    const lastConversationOutsideNewRef = useRef<string | null>(openedConversationId);

    // Child (sub-agent) conversations are deliberately absent from a pod's
    // conversation list, so a tab opened on one has nothing to name itself
    // with: it settles on "Untitled conversation" with no status dot and stays
    // that way — for exactly the conversations a reader most wants to watch
    // from the strip. Whatever the list cannot account for is fetched alone.
    //
    // Computed against the list rather than against the merged result below,
    // so resolving an id never removes the query that resolved it.
    const unlistedConversationIds = useMemo(() => {
        if (!enabled) return [];
        const wanted = new Set<string>();
        tabs.forEach((tab) => {
            if (tab.kind === 'conversation' && tab.resourceId !== 'new') wanted.add(tab.resourceId);
        });
        // The active conversation is known a render before its tab is written,
        // so asking for it here starts the fetch with the navigation rather
        // than one render behind it.
        if (activeTabId?.startsWith('conversation:')) {
            wanted.add(activeTabId.slice('conversation:'.length));
        }
        const listed = new Set(conversations.map((conversation) => conversation.id));
        return [...wanted].filter((id) => Boolean(id) && !listed.has(id));
    }, [activeTabId, conversations, enabled, tabs]);

    const unlistedConversationQueries = useQueries({
        queries: unlistedConversationIds.map((conversationId) => ({
            queryKey: workspaceTabConversationQueryKey(podId, conversationId),
            queryFn: () => getLemmaClient(podId).conversations.get(conversationId),
            // A sub-agent is usually still working when its tab is opened, and
            // the tab's dot is the only place that says so. Poll while it runs;
            // stop dead when it settles.
            refetchInterval: (query: { state: { data?: unknown } }) => (
                isUnsettledConversation(query.state.data) ? UNLISTED_CONVERSATION_POLL_MS : false
            ),
            // A conversation that was deleted, or that this viewer cannot read,
            // will not appear on a retry — and the tab keeps its stored title.
            retry: false,
            staleTime: UNLISTED_CONVERSATION_POLL_MS,
        })),
    });

    // One list for every effect below: whether a conversation arrived in the
    // pod's list or had to be fetched is not their business.
    const resolvedConversations = useMemo(() => {
        const fetched = unlistedConversationQueries
            .map((query) => query.data as Conversation | undefined)
            .filter((conversation): conversation is Conversation => typeof conversation?.id === 'string');
        return fetched.length > 0 ? [...conversations, ...fetched] : conversations;
    }, [conversations, unlistedConversationQueries]);

    // The URL remains canonical. Visiting a route section or conversation opens
    // it in the pod's working set; navigation within a section updates that tab.
    useEffect(() => {
        if (!enabled) return;
        if (!activeTabId || activeTabId === HOME_WORKSPACE_TAB.id) return;

        if (activeTabId === NEW_WORKSPACE_TAB.id) {
            updatePodWorkspaceTabs(
                podId,
                (current) => upsertWorkspaceTab(current, NEW_WORKSPACE_TAB),
            );
            return;
        }

        // App tabs are ephemeral: the focused app's tab is derived for display
        // below, and never written into the working set. Falling through to
        // the conversation branch here used to be impossible when every app
        // route pinned a tab — with pinning gone the guard is load-bearing.
        if (activeTabId.startsWith('app:')) return;

        if (activeTabId.startsWith('route:')) {
            const routeKey = activeTabId.slice('route:'.length);
            const title = routeTitle.trim()
                || formatWorkspaceAppTitle(routeKey);
            updatePodWorkspaceTabs(
                podId,
                (current) => upsertWorkspaceTab(
                    current,
                    routeWorkspaceTab(routeKey, title, currentHref),
                ),
            );
            return;
        }

        const conversationId = activeTabId.slice('conversation:'.length);
        const conversation = resolvedConversations.find((candidate) => candidate.id === conversationId);
        updatePodWorkspaceTabs(podId, (current) => {
            const existing = current.find((tab) => tab.id === activeTabId);
            const nextTab = conversationWorkspaceTab(conversationId, conversation);
            if (!conversation && existing?.kind === 'conversation') {
                nextTab.title = existing.title;
                nextTab.status = existing.status;
            }
            return upsertWorkspaceTab(current, nextTab);
        });
    }, [activeTabId, currentHref, enabled, podId, resolvedConversations, routeTitle]);

    // A new conversation starts without an id. Capture the conversation that was
    // active before entering /new; when a different id appears while that route is
    // still active, the temporary tab can safely become the real tab in place.
    useEffect(() => {
        if (!enabled) return;
        const isNewConversationRoute = activeTabId === NEW_WORKSPACE_TAB.id;
        if (!isNewConversationRoute) {
            wasNewConversationRouteRef.current = false;
            newConversationBaselineRef.current = null;
            lastConversationOutsideNewRef.current = openedConversationId;
            return;
        }

        if (!wasNewConversationRouteRef.current) {
            wasNewConversationRouteRef.current = true;
            newConversationBaselineRef.current = lastConversationOutsideNewRef.current;
        }

        if (
            openedConversationId
            && openedConversationId !== newConversationBaselineRef.current
        ) {
            const conversation = resolvedConversations.find(
                (candidate) => candidate.id === openedConversationId,
            );
            updatePodWorkspaceTabs(podId, (current) => promoteNewConversationTab(
                current,
                openedConversationId,
                conversation,
            ));
        }
    }, [activeTabId, enabled, openedConversationId, podId, resolvedConversations]);

    useEffect(() => {
        if (!enabled) return;
        if (!appsLoaded) return;
        updatePodWorkspaceTabs(
            podId,
            (current) => syncWorkspaceTabMetadata(
                // Clears any app tabs pinned before the rail existed; apps are
                // never kept, so this no longer needs the pages list.
                syncAppWorkspaceTabs(current),
                resolvedConversations,
            ),
        );
    }, [appsLoaded, enabled, podId, resolvedConversations]);

    const closeTab = useCallback((tabId: string) => {
        updatePodWorkspaceTabs(podId, (current) => closeWorkspaceTab(current, tabId));
    }, [podId]);

    // The strip still marks the app you are looking at: while the viewer is
    // focused, its tab is appended for display only. Nothing writes it to the
    // store, so it is gone the moment you navigate anywhere else — a tab you
    // cannot keep, for a thing the rail already keeps.
    const displayTabs = useMemo(() => {
        if (!activeTabId?.startsWith('app:')) return tabs;
        if (tabs.some((tab) => tab.id === activeTabId)) return tabs;

        const activeApp = pages.find((candidate) => candidate.slug === appSlug);
        return [...tabs, appWorkspaceTab(activeApp ?? {
            slug: appSlug ?? '',
            title: formatWorkspaceAppTitle(appSlug),
        })];
    }, [activeTabId, appSlug, pages, tabs]);

    const openAppSlugs = useMemo(
        () => {
            const slugs = new Set(
                tabs.map(getAppSlugFromWorkspaceTab).filter((slug): slug is string => Boolean(slug)),
            );
            if (appSlug) slugs.add(appSlug);
            return [...slugs];
        },
        [appSlug, tabs],
    );

    return {
        tabs: displayTabs,
        activeTabId,
        closeTab,
        openAppSlugs,
    };
}
