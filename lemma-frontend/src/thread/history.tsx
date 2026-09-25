import { LoadingRows } from "@/ui/loading";
import { PlusIcon, ArrowRightIcon, VoiceIcon, ArchiveIcon } from "@/ui/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { lemma } from "@/session/client";
import { source, NEW_CONVERSATION } from "@/data";
import type { Pod } from "@/data";
import { applyArchived, patchConversationLists, unbound } from "./conversation-list";

import { ConversationTitle } from "./conversation-title";

const SHOWN = 5;

/** This teammate's last few conversations, beside their transcript. Five,
 *  never twenty — a panel that lists everything stops being a shortcut and
 *  becomes a wall you scroll past. The rest live behind a tab. */
export function History({
    pod,
    conversationId,
    onPick,
    onSeeAll,
}: {
    pod: Pod;
    conversationId: string | null;
    onPick: (id: string | null) => void;
    onSeeAll: () => void;
}) {
    const history = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
    });

    /* Only for the thread being read. Calls are children, and asking for every
       listed conversation's children would be five requests to decorate a
       panel — this is the one conversation whose calls anybody is looking
       for. */
    const open = conversationId && conversationId !== NEW_CONVERSATION ? conversationId : null;
    const calls = useQuery({
        queryKey: ["call-threads", pod.id, open],
        queryFn: () => source.listCallThreads(pod.id, open as string),
        enabled: Boolean(open),
        staleTime: 30_000,
    });

    const cache = useQueryClient();
    const sample = source.label === "sample";
    const [archiving, setArchiving] = useState<string | null>(null);

    /* Put away, not thrown away — the endpoint is the same one a rename uses,
       and an archived conversation is still readable by id. What changes is
       this list, which is "conversations you can open here". */
    async function archive(id: string) {
        setArchiving(id);
        const undo = patchConversationLists(cache, pod.id, (list) => applyArchived(list, id));
        /* Leave the pane rather than leave it pointed at something that is no
           longer in the list beside it. */
        if (id === conversationId) onPick(NEW_CONVERSATION);
        try {
            /* The sample source is the cache. Patching it is the whole
               mutation there — the same trick the approval card uses to answer
               itself — which is what keeps this control visible and judgeable
               in the one mode that can be looked at without a session. */
            if (!sample) {
                await lemma(pod.id).conversations.update(id, { is_archived: true }, { pod_id: pod.id });
            }
        } catch {
            undo();
        } finally {
            setArchiving(null);
        }
    }

    /* Bound conversations have their own way in — the resource they belong to.
       This panel is for the conversations that have no other front door. */
    const all = unbound(history.data);
    const recent = all.slice(0, SHOWN);
    const rest = all.length - recent.length;

    return (
        <aside className="history" aria-label={"Conversations with " + pod.name}>
            <button
                className="history__new"
                aria-current={conversationId === NEW_CONVERSATION}
                onClick={() => onPick(NEW_CONVERSATION)}
            >
                <PlusIcon size={17} />
                New conversation
            </button>

            {history.isPending && <LoadingRows label="Loading history" />}
            {history.isError && <p className="history__quiet">Couldn’t load conversation history.</p>}

            {recent.length > 0 && (
                <>
                    <div className="history__label">Recent</div>
                    <div className="history__list">
                        {recent.map((entry) => (
                            <div key={entry.id} className="history__entry">
                                <button
                                    className="history__item"
                                    aria-current={entry.id === conversationId}
                                    onClick={() => onPick(entry.id)}
                                    title={entry.title}
                                >
                                    <span className="history__name">{entry.title}</span>
                                    <span className="history__at">{entry.at}</span>
                                </button>
                                <ConversationTitle podId={pod.id} conversationId={entry.id} title={entry.title} />
                                {(
                                    <button
                                        className="history__archive"
                                        title={"Archive " + entry.title}
                                        aria-label={"Archive " + entry.title}
                                        disabled={archiving === entry.id}
                                        onClick={() => void archive(entry.id)}
                                    >
                                        <ArchiveIcon size={14} />
                                    </button>
                                )}
                                {/* What happened inside this conversation, rather
                                    than beside it — so a call is findable where
                                    you would look for what it was about. */}
                                {entry.id === open && (calls.data ?? []).map((call) => (
                                    <button
                                        key={call.id}
                                        className="history__item history__item--call"
                                        aria-current={call.id === conversationId}
                                        onClick={() => onPick(call.id)}
                                        title={call.title}
                                    >
                                        <VoiceIcon size={16} />
                                        <span className="history__name">{call.title}</span>
                                        <span className="history__at">{call.at}</span>
                                    </button>
                                ))}
                            </div>
                        ))}
                    </div>
                </>
            )}

            {rest > 0 && (
                <button className="history__more" onClick={onSeeAll}>
                    {/* No count: this list is the first page, so any number
                        here would be a floor dressed up as a total. */}
                    All conversations <ArrowRightIcon size={15} />
                </button>
            )}
        </aside>
    );
}
