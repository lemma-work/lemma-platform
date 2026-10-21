"use client";

import { useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { useMe } from "@/session/use-me";
import { channelApi } from "./api";
import { openAtLine } from "./focus";
import { channelTitle, senderLabel, sinceShort, type Page } from "./model";
import { hitText, snippet, type SearchHit } from "./search-model";

/** Lines found across the channels the reader may see, a page at a time,
 *  each opening its channel at that line. */
export function SearchResults({ pod, query }: { pod: Pod; query: string }) {
    const me = useMe();
    const [settled, setSettled] = useState(query);
    /* Asked once typing pauses, not per key. */
    useEffect(() => {
        const timer = setTimeout(() => setSettled(query.trim()), 300);
        return () => clearTimeout(timer);
    }, [query]);
    const found = useInfiniteQuery({
        queryKey: ["channel", pod.id, "search", settled] as const,
        enabled: settled.length >= 2,
        queryFn: ({ pageParam }) => channelApi(pod.id).search(settled, { cursor: pageParam }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (last: Page<SearchHit>) => last.nextBefore ?? undefined,
        staleTime: 30_000,
    });
    const hits = (found.data?.pages ?? []).flatMap((page) => page.items);
    if (settled.length < 2) return <p className="channels__quiet">Type at least two letters to search what was said.</p>;
    return (
        <section className="channels__section" aria-label={"Lines matching " + settled}>
            {found.isPending ? (
                <p className="all__empty" role="status">Searching…</p>
            ) : found.isError ? (
                <p className="channels__trouble" role="alert">Couldn’t search. <button type="button" className="gsheet__inline" onClick={() => void found.refetch()}>Try again</button></p>
            ) : hits.length === 0 ? (
                <p className="channels__quiet" role="status">Nothing said matches “{settled}”.</p>
            ) : (
                <>
                    <p className="sr-only" role="status">{hits.length === 1 ? "1 line found" : hits.length + (found.hasNextPage ? " or more" : "") + " lines found"}</p>
                    <ul className="crows chsearch">
                        {hits.map((hit) => {
                            const where = channelTitle({ kind: hit.channel.kind, title: hit.channel.title, person: hit.channel.person, sources: [] }, pod.name);
                            return (
                                <li key={hit.line.id} className="crow chsearch__hit">
                                    <button
                                        type="button"
                                        className="crow__open"
                                        onClick={() => openAtLine(pod.id, hit.channel.id, { lineId: hit.line.id, threadRootId: hit.line.threadRootId })}
                                    >
                                        <span className="crow__name">{senderLabel(hit.line, me)} <small className="chsearch__where">in {where}{hit.line.threadRootId ? ", in a thread" : ""}</small></span>
                                        <span className="crow__sub">
                                            {snippet(hitText(hit), settled).map((piece, index) => piece.match ? <mark key={index}>{piece.text}</mark> : <span key={index}>{piece.text}</span>)}
                                        </span>
                                    </button>
                                    <span className="crow__when">{sinceShort(hit.line.createdAt)}</span>
                                </li>
                            );
                        })}
                    </ul>
                    {found.hasNextPage && (
                        <button type="button" className="ghost-pill channels__more" disabled={found.isFetchingNextPage} onClick={() => void found.fetchNextPage()}>
                            {found.isFetchingNextPage ? "Loading…" : "More results"}
                        </button>
                    )}
                </>
            )}
        </section>
    );
}
