"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { AgentMark } from "./agent-mark";
import { source, type ConversationRef, type Pod } from "@/data";
import { ChannelIcon } from "@/shell/channels";
import { ChatIcon, ClockIcon, FileIcon, PlusIcon, SearchIcon, WorkflowIcon, BellIcon } from "@/ui/icons";
import type { ConversationOrigin, OriginKind } from "@/thread/conversation-origin";

type Filter = "all" | "chats" | "channels" | "automations" | "docs";
const FILTERS: { id: Filter; label: string; kinds: OriginKind[] | null }[] = [
    { id: "all", label: "All", kinds: null },
    { id: "chats", label: "Conversations", kinds: ["chat"] },
    { id: "channels", label: "Channels", kinds: ["channel", "notification"] },
    { id: "automations", label: "Automations", kinds: ["schedule", "workflow", "task"] },
    { id: "docs", label: "On docs", kinds: ["resource"] },
];

function OriginIcon({ origin }: { origin: ConversationOrigin }) {
    if ((origin.kind === "channel" || origin.kind === "notification") && origin.platform) {
        return <ChannelIcon platform={origin.platform} size={16} />;
    }
    if (origin.kind === "schedule") return <ClockIcon size={16} />;
    if (origin.kind === "workflow" || origin.kind === "task") return <WorkflowIcon size={16} />;
    if (origin.kind === "notification") return <BellIcon size={16} />;
    if (origin.kind === "resource") return <FileIcon size={16} />;
    return <ChatIcon size={16} />;
}

/** Every conversation in the space — yours with every bot here, the ones a
 *  channel started, and the ones a schedule or a workflow ran — each saying
 *  where it came from. Paged as you scroll; nothing is capped at a sidebar's
 *  eight. */
export function ChatsPage({ pod, openId, onOpen, onOpenRun, onNew }: {
    pod: Pod;
    openId: string | null;
    onOpen: (id: string) => void;
    onOpenRun: (runId: string) => void;
    onNew: () => void;
}) {
    const [typed, setTyped] = useState("");
    const [search, setSearch] = useState("");
    const [filter, setFilter] = useState<Filter>("all");
    useEffect(() => {
        const timer = window.setTimeout(() => setSearch(typed.trim()), 180);
        return () => window.clearTimeout(timer);
    }, [typed]);

    const pages = useInfiniteQuery({
        queryKey: ["conversations", pod.id, "every", search],
        queryFn: ({ pageParam }) => source.listConversationsPage(pod.id, pageParam, search || undefined, true),
        initialPageParam: null as string | null,
        getNextPageParam: last => last.next,
        placeholderData: keepPreviousData,
    });
    const all = useMemo(() => pages.data?.pages.flatMap(page => page.items) ?? [], [pages.data]);
    /* A chat names its bot by uuid only; the roster turns that into a face. */
    const bots = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const botById = useMemo(() => new Map((bots.data ?? []).filter(bot => bot.id && !bot.front).map(bot => [bot.id, bot])), [bots.data]);
    const wanted = FILTERS.find(each => each.id === filter)?.kinds ?? null;
    const shown = wanted ? all.filter(row => wanted.includes((row.origin ?? { kind: "chat" }).kind)) : all;

    /* Paging on sight of the last row, with a button for when that fails. */
    const more = useRef<HTMLButtonElement>(null);
    const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = pages;
    useEffect(() => {
        const target = more.current;
        if (!target || !hasNextPage || isFetchNextPageError || typeof IntersectionObserver === "undefined") return;
        const seen = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
        });
        seen.observe(target);
        return () => seen.disconnect();
    }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage]);

    return (
        <div className="all chats-page">
            <header className="all__head">
                <h1>Chats</h1>
                <label className="all__search">
                    <SearchIcon size={17} />
                    <input placeholder="Search titles" aria-label="Search conversations" value={typed} onChange={event => setTyped(event.target.value)} />
                </label>
                <button className="all__new-button" onClick={onNew}><PlusIcon size={15} /> New</button>
            </header>
            <div className="all__tabs chats-page__filters" role="tablist" aria-label="Which conversations">
                {FILTERS.map(each => (
                    <button key={each.id} role="tab" aria-selected={filter === each.id} onClick={() => setFilter(each.id)}>{each.label}</button>
                ))}
            </div>

            <table className="all__table chats-page__table">
                <thead>
                    <tr><th>Conversation</th><th className="chats-page__col-source">Came from</th><th className="all__col-when">Last activity</th></tr>
                </thead>
                <tbody>
                    {shown.map((row: ConversationRef) => {
                        const origin = row.origin ?? { kind: "chat" as const, label: "Chat" };
                        return (
                            <tr key={row.id} tabIndex={0} aria-current={row.id === openId ? "true" : undefined}
                                onClick={() => onOpen(row.id)} onKeyDown={event => { if (event.key === "Enter") onOpen(row.id); }}>
                                <td>
                                    <span className="chats-page__title">{row.title}</span>
                                    {row.agentId && botById.get(row.agentId) && (
                                        <span className="chats-page__bot">
                                            <AgentMark pod={pod} agent={botById.get(row.agentId)!} size={16} />
                                            {botById.get(row.agentId)!.label}
                                        </span>
                                    )}
                                </td>
                                <td className="chats-page__col-source">
                                    <span className="chats-page__origin" data-kind={origin.kind}>
                                        <OriginIcon origin={origin} />
                                        <span>{origin.label}</span>
                                        {origin.runId && (
                                            <button className="chats-page__run" onClick={event => { event.stopPropagation(); onOpenRun(origin.runId!); }}>
                                                Open run
                                            </button>
                                        )}
                                    </span>
                                </td>
                                <td className="all__col-when">{row.at}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            {pages.isPending && <p className="all__empty">Loading…</p>}
            {pages.isError && <p className="all__empty">Couldn’t load conversations.</p>}
            {pages.isSuccess && shown.length === 0 && <p className="all__empty">{search ? "Nothing matches that." : "No conversations here yet."}</p>}
            {hasNextPage && (
                <button ref={more} className="chats-page__more" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>
                    {isFetchingNextPage ? "Loading more…" : isFetchNextPageError ? "Couldn’t load more — try again" : "Load more"}
                </button>
            )}
            {!hasNextPage && all.length > 0 && <p className="chats-page__count">{all.length} conversations</p>}
        </div>
    );
}
