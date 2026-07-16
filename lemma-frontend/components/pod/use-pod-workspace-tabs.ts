'use client';

import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';

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
    routeWorkspaceTab,
    serializeWorkspaceTabs,
    syncPinnedAppTabs,
    syncWorkspaceTabMetadata,
    upsertWorkspaceTab,
    type PodWorkspaceTab,
} from '@/lib/pods/workspace-tabs';
import type { Conversation } from '@/lib/types';
import type { AppPageRef } from '@/lib/types/app';

interface UsePodWorkspaceTabsOptions {
    podId: string;
    pathname: string;
    currentHref: string;
    routeTitle: string;
    appSlug: string | null;
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
    const activeTabId = getActiveWorkspaceTabId(podId, pathname, appSlug);
    const wasNewConversationRouteRef = useRef(false);
    const newConversationBaselineRef = useRef<string | null>(null);
    const lastConversationOutsideNewRef = useRef<string | null>(openedConversationId);

    // The URL remains canonical. Visiting a route section or conversation opens
    // it in the pod's working set; navigation within a section updates that tab.
    useEffect(() => {
        if (!activeTabId || activeTabId === HOME_WORKSPACE_TAB.id) return;

        if (activeTabId === NEW_WORKSPACE_TAB.id) {
            updatePodWorkspaceTabs(
                podId,
                (current) => upsertWorkspaceTab(current, NEW_WORKSPACE_TAB),
            );
            return;
        }

        if (activeTabId.startsWith('app:')) {
            const activeApp = pages.find((candidate) => candidate.slug === appSlug);
            if (activeApp) {
                updatePodWorkspaceTabs(
                    podId,
                    (current) => upsertWorkspaceTab(current, appWorkspaceTab(activeApp)),
                );
            }
            return;
        }

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
        const conversation = conversations.find((candidate) => candidate.id === conversationId);
        updatePodWorkspaceTabs(podId, (current) => {
            const existing = current.find((tab) => tab.id === activeTabId);
            const nextTab = conversationWorkspaceTab(conversationId, conversation);
            if (!conversation && existing?.kind === 'conversation') {
                nextTab.title = existing.title;
                nextTab.status = existing.status;
            }
            return upsertWorkspaceTab(current, nextTab);
        });
    }, [activeTabId, appSlug, conversations, currentHref, pages, podId, routeTitle]);

    // A new conversation starts without an id. Capture the conversation that was
    // active before entering /new; when a different id appears while that route is
    // still active, the temporary tab can safely become the real tab in place.
    useEffect(() => {
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
            const conversation = conversations.find(
                (candidate) => candidate.id === openedConversationId,
            );
            updatePodWorkspaceTabs(podId, (current) => promoteNewConversationTab(
                current,
                openedConversationId,
                conversation,
            ));
        }
    }, [activeTabId, conversations, openedConversationId, podId]);

    useEffect(() => {
        if (!appsLoaded) return;
        updatePodWorkspaceTabs(
            podId,
            (current) => syncWorkspaceTabMetadata(
                syncPinnedAppTabs(current, pages),
                conversations,
            ),
        );
    }, [appsLoaded, conversations, pages, podId]);

    const closeTab = useCallback((tabId: string) => {
        updatePodWorkspaceTabs(podId, (current) => closeWorkspaceTab(current, tabId));
    }, [podId]);

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
        tabs,
        activeTabId,
        closeTab,
        openAppSlugs,
    };
}
