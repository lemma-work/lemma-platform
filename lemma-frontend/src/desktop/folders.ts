import { useCallback, useEffect, useState } from "react";
import { desktopBridgeAvailable, invoke } from "./bridge";

/** The folder on this computer a conversation works in.
 *
 *  Only on a local install, and only through the desktop shell. The path is
 *  never handed *to* the shell by this page: `bind` raises a native folder
 *  dialog, the shell records the answer itself and returns it only so the chip
 *  can show a name. Nothing here can name a directory, which is the point — a
 *  page that could would be a page that could point an agent anywhere.
 *
 *  A conversation being composed has no id yet, so the choice is parked in the
 *  shell under an id belonging to *this composer* and adopted once the
 *  conversation exists. Per composer, not one shared slot: a folder chosen in
 *  an abandoned composer was otherwise adopted by the next new conversation —
 *  a directory chosen for something else and walked away from.
 */

/** Is choosing a folder something this installation can do at all? */
export function canBindConversationFolder(): boolean {
    return desktopBridgeAvailable();
}

function asPath(value: unknown): string | null {
    return typeof value === "string" && value.length > 0 ? value : null;
}

/** Which slot a call is about: the conversation once there is one, else this
 *  composer's parked choice. */
export interface FolderSlot {
    conversationId: string | null;
    pendingId: string;
}

/** The bound folder, or null for the ordinary Lemma directory. A shell that
 *  would not answer is treated as nothing bound; the run resolves it anyway. */
export async function readFolder(slot: FolderSlot): Promise<string | null> {
    if (!canBindConversationFolder()) return null;
    try {
        return asPath(await invoke("conversation_folder", { ...slot }));
    } catch {
        return null;
    }
}

/** Raise the folder dialog. The choice, or null if it was dismissed. */
export async function bindFolder(slot: FolderSlot): Promise<string | null> {
    if (!canBindConversationFolder()) return null;
    return asPath(await invoke("bind_conversation_folder", { ...slot }));
}

/** Work in the ordinary place again. */
export async function unbindFolder(slot: FolderSlot): Promise<void> {
    if (!canBindConversationFolder()) return;
    await invoke("unbind_conversation_folder", { ...slot });
}

/** Give a parked choice the conversation it was made for.
 *
 *  Call it once the conversation exists and before its first run starts, since
 *  the run is what reads the folder. Silent when nothing was parked, which is
 *  most conversations. Never throws: the conversation still runs, in the
 *  ordinary directory, and failing the first message over a folder would be
 *  the worse outcome. */
export async function adoptConversationFolder(conversationId: string, pendingId: string): Promise<void> {
    if (!canBindConversationFolder()) return;
    try {
        await invoke("adopt_conversation_folder", { conversationId, pendingId });
    } catch {
        /* See above. */
    }
}

export function newPendingId(): string {
    try {
        if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    } catch {
        /* falls through */
    }
    return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** The last path segment, which is all a chip can fit. */
export function folderLabel(path: string): string {
    const parts = path.split(/[/\\]/).filter(Boolean);
    return parts[parts.length - 1] || path;
}

export interface ConversationFolder {
    folder: string | null;
    available: boolean;
    pendingId: string;
    bind: () => Promise<string | null>;
    unbind: () => Promise<void>;
}

/** @param conversationId The conversation, or null while it is being composed. */
export function useConversationFolder(conversationId: string | null): ConversationFolder {
    const [folder, setFolder] = useState<string | null>(null);
    const [available, setAvailable] = useState(false);
    /* One per mounted composer; opaque to the shell, which only uses it to
       keep one composer's waiting choice apart from another's. */
    const [pendingId] = useState(newPendingId);

    useEffect(() => {
        const can = canBindConversationFolder();
        setAvailable(can);
        if (!can) return;
        let cancelled = false;
        void readFolder({ conversationId, pendingId }).then((path) => {
            if (!cancelled) setFolder(path);
        });
        return () => {
            cancelled = true;
        };
    }, [conversationId, pendingId]);

    const bind = useCallback(async () => {
        const chosen = await bindFolder({ conversationId, pendingId });
        /* Dismissing the dialog changes nothing, so the chip must not clear. */
        if (chosen !== null) setFolder(chosen);
        return chosen;
    }, [conversationId, pendingId]);

    const unbind = useCallback(async () => {
        await unbindFolder({ conversationId, pendingId });
        setFolder(null);
    }, [conversationId, pendingId]);

    return { folder, available, pendingId, bind, unbind };
}
