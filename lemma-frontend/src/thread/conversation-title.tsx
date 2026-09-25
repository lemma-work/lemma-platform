"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { EditIcon } from "@/ui/icons";
import { lemma } from "@/session/client";
import { source } from "@/data";
import { applyTitle, patchConversationLists, titleToSend, titleToShow } from "./conversation-list";

/** Inline renaming beside a conversation in the history sidebar. */
export function ConversationTitle({
    podId,
    conversationId,
    title,
}: {
    podId: string;
    conversationId: string | null;
    /** What the list currently holds for this conversation, if anything. */
    title: string | null;
}) {
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState("");
    const [saving, setSaving] = useState(false);
    const [failed, setFailed] = useState(false);
    const input = useRef<HTMLInputElement | null>(null);
    const sample = source.label === "sample";

    useEffect(() => {
        if (editing) input.current?.select();
    }, [editing]);

    /* Leaving the conversation abandons an unsaved rename rather than carrying
       it onto the next one, which is what a draft keyed on nothing would do. */
    useEffect(() => {
        setEditing(false);
        setFailed(false);
    }, [conversationId]);

    if (!conversationId) return null;

    const shown = titleToShow(title, "New conversation");

    async function save() {
        const next = titleToSend(draft);
        setEditing(false);
        if (next === (title ?? null) || (next === null && !title)) return;

        /* Optimistic, and reverted below if the server disagrees. A rename that
           waits for a round trip to appear reads as a click that missed. */
        patchConversationLists(cache, podId, (list) => applyTitle(list, conversationId!, next));
        setSaving(true);
        setFailed(false);
        try {
            /* Sample mode has no server to tell; the cache it just patched is
               the whole of its state. Gating the control on sample instead
               would leave this row unjudgeable everywhere it can be seen. */
            if (!sample) {
                await lemma(podId).conversations.update(conversationId!, { title: next }, { pod_id: podId });
            }
        } catch {
            void cache.invalidateQueries({ queryKey: ["conversations", podId] });
            setFailed(true);
        } finally {
            setSaving(false);
        }
    }

    if (editing) {
        return (
            <div className="history__rename history__rename--editing">
                <input
                    ref={input}
                    className="history__rename-input"
                    aria-label="Conversation title"
                    value={draft}
                    placeholder="Leave empty to let it name itself"
                    maxLength={200}
                    onChange={(event) => setDraft(event.target.value)}
                    onBlur={() => void save()}
                    onKeyDown={(event) => {
                        if (event.key === "Enter") { event.preventDefault(); void save(); }
                        if (event.key === "Escape") { event.preventDefault(); setEditing(false); }
                    }}
                />
            </div>
        );
    }

    return (
        <div className="history__rename">
            <button
                className="history__rename-button"
                title={"Rename " + shown}
                aria-label={"Rename " + shown}
                disabled={saving}
                onClick={() => { setDraft(title ?? ""); setEditing(true); }}
            >
                <EditIcon size={14} />
            </button>
            {failed && (
                <span className="history__rename-failed" role="status">
                    not renamed
                </span>
            )}
        </div>
    );
}
