"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source, type ConversationRef } from "@/data";
import { applyTitle, titleToSend, titleToShow } from "./conversation-list";

/** What this conversation is about, above the conversation.
 *
 *  It had nowhere to live before. The title existed — the backend generates one
 *  after the first run completes — but it was only ever drawn in the history
 *  panel, so the one conversation you were actually reading was the one thing
 *  on screen that would not say what it was. The header above it carries the
 *  teammate's name, which is the same on every conversation with them.
 *
 *  Editable in place, because renaming is the kind of thing people do while
 *  reading rather than by going somewhere. Blank means "you pick" — see
 *  `titleToSend` for why that is `null` and not `""`.
 */
export function ConversationTitle({
    podId,
    conversationId,
    title,
    busy,
}: {
    podId: string;
    conversationId: string | null;
    /** What the list currently holds for this conversation, if anything. */
    title: string | null;
    /** A run is going. The title still renames; this only softens the row. */
    busy?: boolean;
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

        const key = ["conversations", podId];
        const previous = cache.getQueryData<ConversationRef[]>(key);
        /* Optimistic, and reverted below if the server disagrees. A rename that
           waits for a round trip to appear reads as a click that missed. */
        cache.setQueryData(key, applyTitle(previous, conversationId!, next));
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
            cache.setQueryData(key, previous);
            setFailed(true);
        } finally {
            setSaving(false);
        }
    }

    if (editing) {
        return (
            <div className="convo-title convo-title--editing">
                <input
                    ref={input}
                    className="convo-title__input"
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
        <div className="convo-title" data-busy={busy ? "" : undefined}>
            <button
                className="convo-title__name convo-title__name--editable"
                title="Rename this conversation"
                disabled={saving}
                onClick={() => { setDraft(title ?? ""); setEditing(true); }}
            >
                {shown}
            </button>
            {failed && (
                <span className="convo-title__failed" role="status">
                    not renamed
                </span>
            )}
        </div>
    );
}
