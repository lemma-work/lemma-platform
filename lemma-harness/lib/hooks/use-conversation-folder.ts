'use client';

/**
 * The folder on this computer a conversation works in.
 *
 * Only ever answers on a local install, and only through the desktop shell.
 * The path is never handed to this page: `bind` raises a native folder dialog
 * in the shell, which records the answer itself and returns it only so the chip
 * can show a name. Nothing here can name a directory, which is the point — a
 * page that could would be a page that could point an agent anywhere.
 *
 * A conversation being composed has no id yet, so the choice is parked in the
 * shell under an id belonging to *this composer*, and adopted once the
 * conversation exists. Per composer rather than one shared slot: a folder
 * chosen in a composer that was then abandoned used to still be sitting there
 * when the next new conversation started, and that conversation adopted it —
 * a directory chosen for something else and walked away from.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { isLocalDeployment } from '@/lib/config';

type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

function shellInvoke(): Invoke | null {
    if (typeof window === 'undefined') return null;
    const invoke = window.__TAURI__?.core?.invoke;
    if (typeof invoke !== 'function' || !isLocalDeployment()) return null;
    return invoke as Invoke;
}

/** Is choosing a folder something this installation can do at all? */
export function canBindConversationFolder(): boolean {
    return shellInvoke() !== null;
}

function asPath(value: unknown): string | null {
    return typeof value === 'string' && value.length > 0 ? value : null;
}

export interface ConversationFolder {
    /** This composer's slot for a choice made before the conversation exists. */
    pendingId: string;
    /** The bound folder, or null for the ordinary Lemma directory. */
    folder: string | null;
    /** Available only on a local install, through the desktop shell. */
    available: boolean;
    /** Raise the folder dialog. Resolves to the choice, or null if dismissed. */
    bind: () => Promise<string | null>;
    /** Work in the ordinary place again. */
    unbind: () => Promise<void>;
}

/**
 * @param conversationId The conversation, or null while it is being composed.
 */
export function useConversationFolder(conversationId: string | null): ConversationFolder {
    const [folder, setFolder] = useState<string | null>(null);
    const available = canBindConversationFolder();
    // One per mounted composer. Opaque to the shell, which only uses it to keep
    // one composer's waiting choice apart from another's.
    const pendingId = useMemo(() => newPendingId(), []);

    useEffect(() => {
        const invoke = shellInvoke();
        if (!invoke) return;
        let cancelled = false;
        void invoke('conversation_folder', { conversationId, pendingId })
            .then((value) => {
                if (!cancelled) setFolder(asPath(value));
            })
            .catch(() => {
                // The shell is there but would not answer. Nothing is bound as
                // far as this page is concerned; the run resolves it anyway.
                if (!cancelled) setFolder(null);
            });
        return () => {
            cancelled = true;
        };
    }, [conversationId, pendingId]);

    const bind = useCallback(async () => {
        const invoke = shellInvoke();
        if (!invoke) return null;
        const chosen = asPath(await invoke('bind_conversation_folder', { conversationId, pendingId }));
        // Dismissing the dialog changes nothing, so the chip must not clear.
        if (chosen !== null) setFolder(chosen);
        return chosen;
    }, [conversationId, pendingId]);

    const unbind = useCallback(async () => {
        const invoke = shellInvoke();
        if (!invoke) return;
        await invoke('unbind_conversation_folder', { conversationId, pendingId });
        setFolder(null);
    }, [conversationId, pendingId]);

    return { folder, available, bind, unbind, pendingId };
}

/**
 * Give a parked choice the conversation it was made for.
 *
 * Called after the conversation exists. Silent when nothing was parked, which
 * is the ordinary case: most conversations never pick a folder.
 */
export async function adoptConversationFolder(
    conversationId: string,
    pendingId: string,
): Promise<void> {
    const invoke = shellInvoke();
    if (!invoke) return;
    try {
        await invoke('adopt_conversation_folder', { conversationId, pendingId });
    } catch {
        // The conversation still runs, in the ordinary directory. Failing the
        // first message over a folder choice would be the worse outcome.
    }
}

function newPendingId(): string {
    try {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    } catch {
        // Falls through.
    }
    return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** The last path segment, which is what a chip has room for. */
export function folderLabel(path: string): string {
    const parts = path.split(/[/\\]/).filter(Boolean);
    return parts[parts.length - 1] || path;
}
