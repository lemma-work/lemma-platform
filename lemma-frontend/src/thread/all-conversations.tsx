import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import type { Pod } from "@/data";
import { ConversationTitle } from "./conversation-title";

/** Every conversation this teammate has had. The panel beside the transcript
 *  shows the last few; this is where the rest live, so the panel never grows
 *  into a wall of dead rows. */
export function AllConversations({
    pod,
    conversationId,
    onPick,
}: {
    pod: Pod;
    conversationId: string | null;
    onPick: (id: string) => void;
}) {
    const [filter, setFilter] = useState("");
    const history = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
    });

    const entries = useMemo(() => {
        const all = history.data ?? [];
        const needle = filter.trim().toLowerCase();
        return needle ? all.filter((entry) => entry.title.toLowerCase().includes(needle)) : all;
    }, [history.data, filter]);

    return (
        <div className="pane">
            <div className="pane__inner">
                <div className="all__head">
                    <h2>Conversations with {pod.name}</h2>
                    <input
                        className="all__filter"
                        placeholder="Filter…"
                        value={filter}
                        onChange={(event) => setFilter(event.target.value)}
                    />
                </div>

                {history.isPending && <p className="empty-row">Reading history…</p>}
                {history.isError && <p className="empty-row">Couldn’t load conversation history.</p>}
                {history.isSuccess && entries.length === 0 && (
                    <p className="empty-row">{filter ? "Nothing matches that." : "No conversations yet."}</p>
                )}

                <div className="all__list">
                    {entries.map((entry) => (
                        <div key={entry.id} className="history__entry all__entry">
                            <button
                                className="all__row"
                                aria-current={entry.id === conversationId}
                                onClick={() => onPick(entry.id)}
                            >
                                <span className="all__name">{entry.title}</span>
                                {entry.kind !== "CHAT" && <span className="all__kind">{entry.kind.toLowerCase()}</span>}
                                <span className="all__at">{entry.at}</span>
                            </button>
                            <ConversationTitle podId={pod.id} conversationId={entry.id} title={entry.title} />
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
