import { LoadingRows } from "@/ui/loading";
import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { source } from "@/data";
import type { Pod } from "@/data";
import { ConversationTitle } from "./conversation-title";
import { allConversationsKey } from "./conversation-list";

/** Every conversation this teammate has had. The panel beside the transcript
 *  shows the last few; this is where the rest live, so the panel never grows
 *  into a wall of dead rows.
 *
 *  A page at a time, most recently active first. The next page loads when the
 *  "More" button scrolls into view, and the button is still a button for
 *  anyone who gets there without scrolling.
 *
 *  The filter is a search on the server, not over the loaded pages: filtering
 *  what had loaded left the "More" button in view whenever little matched, and
 *  the pane then paged through the whole history to fill the screen. */
/** Long enough that a word is typed rather than sent a letter at a time;
 *  the same pause the global search uses. */
const SEARCH_DEBOUNCE_MS = 180;

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
    /* What the server is asked for: the filter once typing pauses, so a word
       is one request rather than one per letter. */
    const [search, setSearch] = useState("");
    useEffect(() => {
        const timer = window.setTimeout(() => setSearch(filter.trim()), SEARCH_DEBOUNCE_MS);
        return () => window.clearTimeout(timer);
    }, [filter]);

    const history = useInfiniteQuery({
        queryKey: allConversationsKey(pod.id, search),
        queryFn: ({ pageParam }) => source.listConversationsPage(pod.id, pageParam, search || undefined),
        initialPageParam: null as string | null,
        getNextPageParam: (last) => last.next,
        /* The last results stay up while the next search runs, rather than the
           list blinking to a loading state between words. */
        placeholderData: keepPreviousData,
    });
    const { isFetchingNextPage, isFetchNextPageError, fetchNextPage } = history;
    /* Not while the previous search's rows are standing in: their "next" is a
       page of a different search. */
    const hasNextPage = history.hasNextPage && !history.isPlaceholderData;

    const entries = useMemo(() => history.data?.pages.flatMap((page) => page.items) ?? [], [history.data]);

    const more = useRef<HTMLButtonElement>(null);
    useEffect(() => {
        const target = more.current;
        /* Not after a failure: the button stays in view, so re-observing would
           ask for the failing page again and again. Pressing it retries. */
        if (!target || !hasNextPage || isFetchNextPageError || typeof IntersectionObserver === "undefined") return;
        const observer = new IntersectionObserver((seen) => {
            if (seen.some((entry) => entry.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
        });
        observer.observe(target);
        return () => observer.disconnect();
    }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage]);

    const emptyText = search ? "Nothing matches that." : "No conversations yet.";

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

                {history.isPending && <LoadingRows label="Loading history" rows={5} />}
                {history.isError && <p className="empty-row">Couldn’t load conversation history.</p>}
                {history.isSuccess && !history.isPlaceholderData && entries.length === 0 && (
                    <p className="empty-row">{emptyText}</p>
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

                {hasNextPage && (
                    <button
                        ref={more}
                        className="history__more"
                        disabled={isFetchingNextPage}
                        onClick={() => void fetchNextPage()}
                    >
                        {isFetchingNextPage ? "Loading…" : "More"}
                    </button>
                )}
                {history.isFetchNextPageError && <p className="empty-row">Couldn’t load older conversations.</p>}
            </div>
        </div>
    );
}
