'use client';

import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';
import { useQueries } from '@tanstack/react-query';
import { ApiError } from 'lemma-sdk';

import { getLemmaClient } from '../sdk/lemma-client';
import type { Conversation } from '../types';
import { useProfile } from './use-user';
import {
    parsePinnedIds,
    pinnedStorageKey,
    togglePinnedId,
    withoutPinnedId,
} from '../assistant/pinned-conversations';

/**
 * `localStorage` is the store and React subscribes to it, rather than React
 * holding a copy and writing through. The copy was the version of this that
 * had to be seeded from an effect on mount — which is a cascading render, and
 * which quietly disagreed with itself once the same pod was open in two tabs.
 * Subscribing gets the `storage` event for free, so a pin made in one tab shows
 * up in the other.
 */
const listeners = new Set<() => void>();

function subscribeToPins(listener: () => void): () => void {
    listeners.add(listener);
    window.addEventListener('storage', listener);
    return () => {
        listeners.delete(listener);
        window.removeEventListener('storage', listener);
    };
}

function readPins(storageKey: string | null): string | null {
    if (!storageKey || typeof window === 'undefined') return null;
    try {
        return window.localStorage.getItem(storageKey);
    } catch {
        // Private-mode and blocked-storage browsers throw on read. No pins is
        // the right answer, and it is not worth an error in front of anybody.
        return null;
    }
}

function writePins(storageKey: string | null, ids: string[]): void {
    if (!storageKey) return;
    try {
        window.localStorage.setItem(storageKey, JSON.stringify(ids));
    } catch {
        // A full or blocked store costs this session its pins and nothing else.
    }
    // Same-tab subscribers do not get a `storage` event -- that one only fires
    // for the other tabs -- so the writer tells them.
    listeners.forEach((listener) => listener());
}

/**
 * The pinned conversations for this pod, in pin order.
 *
 * `known` is whatever the caller already has on screen — the sidebar's fifteen.
 * Pins found there cost nothing; the rest are fetched by id, which is the whole
 * reason the ids can live on the device: the list endpoint never has to know
 * about them, and a pin older than the page still resolves.
 *
 * An id that cannot resolve is dropped. A conversation can stop being reachable
 * (the pod was left, the row went another way), and a pin that will never
 * resolve would otherwise hold a permanent gap in the group.
 */
export function usePinnedConversations(podId: string, known: Conversation[]) {
    const { data: profile } = useProfile();
    const userId = profile?.id ?? null;
    const storageKey = userId ? pinnedStorageKey(userId, podId) : null;

    const raw = useSyncExternalStore(
        subscribeToPins,
        () => readPins(storageKey),
        // The server has no pins to report, and saying so is what keeps the
        // first client render identical to the markup it hydrates.
        () => null,
    );
    const pinnedIds = useMemo(() => parsePinnedIds(raw), [raw]);

    const knownById = useMemo(() => {
        const index = new Map<string, Conversation>();
        known.forEach((conversation) => index.set(conversation.id, conversation));
        return index;
    }, [known]);

    const missingIds = useMemo(
        () => pinnedIds.filter((id) => !knownById.has(id)),
        [knownById, pinnedIds],
    );

    const fetched = useQueries({
        queries: missingIds.map((id) => ({
            queryKey: ['conversations', id] as const,
            queryFn: () =>
                getLemmaClient(podId).conversations.get(id, { pod_id: podId }) as Promise<Conversation>,
            enabled: Boolean(podId),
            retry: false,
        })),
    });

    const fetchedById = useMemo(() => {
        const index = new Map<string, Conversation>();
        fetched.forEach((query) => {
            if (query.data) index.set(query.data.id, query.data);
        });
        return index;
    }, [fetched]);

    // Only a definite answer prunes -- gone, or not ours. A flaky connection
    // must never quietly empty somebody's pins.
    const unresolvableKey = fetched
        .map((query, index) => ({ id: missingIds[index], error: query.error }))
        .filter(({ error }) =>
            error instanceof ApiError && (error.statusCode === 404 || error.statusCode === 403),
        )
        .map(({ id }) => id)
        .join(',');

    useEffect(() => {
        if (!unresolvableKey) return;
        const gone = new Set(unresolvableKey.split(','));
        const next = pinnedIds.filter((id) => !gone.has(id));
        if (next.length !== pinnedIds.length) writePins(storageKey, next);
    }, [pinnedIds, storageKey, unresolvableKey]);

    const pinned = useMemo(
        () =>
            pinnedIds
                .map((id) => knownById.get(id) ?? fetchedById.get(id))
                .filter((conversation): conversation is Conversation => Boolean(conversation))
                // A conversation that has been put away is not one you are
                // keeping at hand, wherever it was archived from.
                .filter((conversation) => !conversation.is_archived),
        [fetchedById, knownById, pinnedIds],
    );

    const isPinned = useCallback((id: string) => pinnedIds.includes(id), [pinnedIds]);
    const toggle = useCallback(
        (id: string) => writePins(storageKey, togglePinnedId(pinnedIds, id)),
        [pinnedIds, storageKey],
    );
    const unpin = useCallback(
        (id: string) => writePins(storageKey, withoutPinnedId(pinnedIds, id)),
        [pinnedIds, storageKey],
    );

    return { pinned, pinnedIds, isPinned, toggle, unpin, canPin: Boolean(storageKey) };
}
