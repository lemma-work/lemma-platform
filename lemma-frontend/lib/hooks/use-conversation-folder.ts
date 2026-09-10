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
 * A conversation being composed has no id yet, so `bind(null)` parks the choice
 * in the shell and `adopt(id)` gives it one once the conversation exists.
 */

import { useCallback, useEffect, useState } from 'react';

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

    useEffect(() => {
        const invoke = shellInvoke();
        if (!invoke) return;
        let cancelled = false;
        void invoke('conversation_folder', { conversationId })
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
    }, [conversationId]);

    const bind = useCallback(async () => {
        const invoke = shellInvoke();
        if (!invoke) return null;
        const chosen = asPath(await invoke('bind_conversation_folder', { conversationId }));
        // Dismissing the dialog changes nothing, so the chip must not clear.
        if (chosen !== null) setFolder(chosen);
        return chosen;
    }, [conversationId]);

    const unbind = useCallback(async () => {
        const invoke = shellInvoke();
        if (!invoke) return;
        await invoke('unbind_conversation_folder', { conversationId });
        setFolder(null);
    }, [conversationId]);

    return { folder, available, bind, unbind };
}

/**
 * Give a parked choice the conversation it was made for.
 *
 * Called after the conversation exists. Silent when nothing was parked, which
 * is the ordinary case: most conversations never pick a folder.
 */
export async function adoptConversationFolder(conversationId: string): Promise<void> {
    const invoke = shellInvoke();
    if (!invoke) return;
    try {
        await invoke('adopt_conversation_folder', { conversationId });
    } catch {
        // The conversation still runs, in the ordinary directory. Failing the
        // first message over a folder choice would be the worse outcome.
    }
}

/** The last path segment, which is what a chip has room for. */
export function folderLabel(path: string): string {
    const parts = path.split(/[/\\]/).filter(Boolean);
    return parts[parts.length - 1] || path;
}
