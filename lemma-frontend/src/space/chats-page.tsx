"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { AgentMark } from "./agent-mark";
import { source, type ConversationRef, type Pod } from "@/data";
import { ChannelIcon } from "@/shell/channels";
import { AgentIcon, ChatIcon, ClockIcon, FileIcon, SearchIcon, WorkflowIcon, BellIcon } from "@/ui/icons";
import { ChannelMark, StartChat } from "@/channels/chat-starter";
import { chatRows, listedAsChat } from "@/channels/chat-list";
import { placeName } from "@/channels/chat-copy";
import { badge, channelTitle, sayList, type Channel } from "@/channels/model";
import { allChannels, useChannelList } from "@/channels/queries";
import { SearchResults } from "@/channels/search";
import { listStamp } from "@/data/stamp";
import { originLabel, type ConversationOrigin, type OriginKind } from "@/thread/conversation-origin";
import { emptyFor } from "./empty-copy";
import { SpaceEmpty } from "./empty-state";

type Filter = "all" | "chats" | "channels" | "automations" | "docs";
const FILTERS: { id: Filter; label: string; kinds: OriginKind[] | null }[] = [
    { id: "all", label: "All", kinds: null },
    { id: "chats", label: "Just you", kinds: ["chat", "ask"] },
    { id: "channels", label: "Shared", kinds: ["channel", "notification"] },
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
    if (origin.kind === "ask") return <AgentIcon size={16} />;
    return <ChatIcon size={16} />;
}

/** Every chat in the space -- yours with every bot here, the ones shared
 *  with people or mirrored from a group, and the ones a schedule or a
 *  workflow ran -- each saying where it came from. Paged as you scroll;
 *  nothing is capped at a sidebar's ten. */
export function ChatsPage({ pod, openId, onOpen, onOpenRun, onNew, onReach, onPages, onAsk, initialFilter = "all", onOpenChannel, onConnect }: {
    pod: Pod;
    openId: string | null;
    onOpen: (id: string) => void;
    /** Which chats it opens on: every one, or only the shared ones. */
    initialFilter?: Filter;
    /** A chat that lives on a channel: shared, mirrored, or your default. */
    onOpenChannel: (channelId: string) => void;
    /** The space's bots, to connect one before bringing it into a group. */
    onConnect: () => void;
    onOpenRun: (runId: string) => void;
    onNew: () => void;
    /** Where channels are connected, for a list with none on it yet. */
    onReach?: () => void;
    onPages?: () => void;
    /** Put words for the teammate in the chat box, to finish or send. */
    onAsk?: (text: string) => void;
}) {
    const [typed, setTyped] = useState("");
    const [search, setSearch] = useState("");
    const [filter, setFilter] = useState<Filter>(initialFilter);
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
    const conversations = filter === "channels" ? [] : wanted ? all.filter(row => wanted.includes((row.origin ?? { kind: "chat" }).kind)) : all;
    /* The chats that live on a channel, in the same list: under All by when
       they were last active, and alone under Shared. Searched by name, as
       conversations are by title. */
    const channelList = useChannelList(pod.id, { archived: false });
    const channels = useMemo(() => {
        if (filter !== "all" && filter !== "channels") return [];
        const needle = search.toLowerCase();
        return allChannels(channelList.data).filter(listedAsChat).filter((channel) => !needle || channelTitle(channel, pod.name).toLowerCase().includes(needle));
    }, [filter, search, channelList.data, pod.name]);
    const rows = useMemo(() => chatRows(conversations, channels, pod.name), [conversations, channels, pod.name]);
    const shown = rows;

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
                    <input placeholder="Search chats" aria-label="Search chats and what was said in shared ones" value={typed} onChange={event => setTyped(event.target.value)} />
                </label>
                <StartChat pod={pod} look="pill" onNewChat={onNew} onOpenChannel={onOpenChannel} onConnect={onConnect} />
            </header>
            <div className="all__tabs chats-page__filters" role="tablist" aria-label="Which conversations">
                {FILTERS.map(each => (
                    <button key={each.id} role="tab" aria-selected={filter === each.id} onClick={() => setFilter(each.id)}>{each.label}</button>
                ))}
            </div>

            {shown.length > 0 && <table className="all__table chats-page__table">
                <thead>
                    <tr><th>Conversation</th><th className="chats-page__col-source">Came from</th><th className="all__col-when">Last activity</th></tr>
                </thead>
                <tbody>
                    {shown.map((each) => {
                        if (each.kind === "channel") return <SharedRow key={each.id} pod={pod} channel={each.channel} title={each.title} onOpen={() => onOpenChannel(each.channel.id)} />;
                        const row: ConversationRef = each.chat;
                        const origin = row.origin ?? { kind: "chat" as const, label: "Chat" };
                        const cameFrom = originLabel(origin, pod.name);
                        return (
                            <tr key={row.id} tabIndex={0} aria-current={row.id === openId ? "true" : undefined}
                                onClick={() => onOpen(row.id)} onKeyDown={event => { if (event.key === "Enter") onOpen(row.id); }}>
                                <td>
                                    <span className="chats-page__title">{row.title}</span>
                                    {row.waitingOnYou && <span className="chats-page__waiting">Needs you</span>}
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
                                        <span title={cameFrom}>{cameFrom}</span>
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
            </table>}
            {search.length >= 2 && (
                <section className="chats-page__said" aria-label="Messages in shared chats">
                    <h2>Said in shared chats</h2>
                    <SearchResults pod={pod} query={search} />
                </section>
            )}
            {pages.isPending && <p className="all__empty">Loading…</p>}
            {pages.isError && <p className="all__empty">Couldn’t load conversations.</p>}
            {pages.isSuccess && (filter !== "channels" || !channelList.isPending) && shown.length === 0 && search && <p className="all__empty">Nothing matches that.</p>}
            {pages.isSuccess && (filter !== "channels" || !channelList.isPending) && shown.length === 0 && !search && (
                <SpaceEmpty empty={emptyFor({ place: "chats", filter }, pod.teammate?.name || pod.name)}
                    on={{ chat: onNew, reach: onReach, pages: onPages, ask: onAsk }} />
            )}
            {hasNextPage && filter !== "channels" && (
                <button ref={more} className="chats-page__more" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>
                    {isFetchingNextPage ? "Loading more…" : isFetchNextPageError ? "Couldn’t load more — try again" : "Load more"}
                </button>
            )}
            {!hasNextPage && shown.length > 0 && <p className="chats-page__count">{shown.length === 1 ? "1 chat" : shown.length + " chats"}</p>}
        </div>
    );
}

/** A chat that lives on a channel, as a row of the table: its name and what
 *  is new in it, where it is mirrored from -- or that it is shared, or your
 *  private line -- and when it was last active. */
function SharedRow({ pod, channel, title, onOpen }: { pod: Pod; channel: Channel; title: string; onOpen: () => void }) {
    const count = badge(channel.unread.lines);
    const places = channel.sources.map((one) => placeName(one));
    const cameFrom = channel.kind === "DIRECT"
        ? "Your private line with " + (pod.teammate?.name || pod.name)
        : places.length ? sayList(places) : "Shared chat";
    return (
        <tr tabIndex={0} onClick={onOpen} onKeyDown={event => { if (event.key === "Enter") onOpen(); }}>
            <td>
                <span className="chats-page__title">{title}</span>
                {channel.unread.waiting > 0 && <span className="chats-page__waiting">Needs you</span>}
                {count && <span className="chats-page__unread" aria-label={count + " new"}>{count}</span>}
            </td>
            <td className="chats-page__col-source">
                <span className="chats-page__origin" data-kind="shared">
                    <ChannelMark channel={channel} />
                    <span title={cameFrom}>{cameFrom}</span>
                </span>
            </td>
            <td className="all__col-when">{listStamp(channel.lastMessageAt ?? channel.createdAt)}</td>
        </tr>
    );
}
