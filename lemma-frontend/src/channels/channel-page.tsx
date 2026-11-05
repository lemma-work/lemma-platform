"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden, isMissing } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { ChannelIcon } from "@/shell/channels";
import { ChevronDownIcon, PeopleIcon } from "@/ui/icons";
import { Avatar, ChatFaces } from "./avatar";
import { AgentMark } from "@/space/agent-mark";
import type { PersonRef } from "./waiting";
import { ChannelMark } from "./chat-mark";
import { channelAbilities } from "./abilities";
import { channelApi } from "./api";
import { ChatDetails, type DetailsTab } from "./chat-details";
import { Composer } from "./composer";
import { ContactControls } from "./contact-controls";
import { onFocusAsked, takeFocus, type LineFocus } from "./focus";
import { answeringNews, sayTyping, typingIn, upsertTimeline } from "./live";
import { channelReaders, mentionables } from "./mentions";
import { channelTitle, nameless, newestSeq, personName, sayList, sinceShort, type ChannelDetail, type Line, type LinePage, type Member } from "./model";
import { answersHint, placeName, sharedWith } from "./chat-copy";
import { platformName } from "./sources";
import { keepChannel, linesKey, refreshLists, useChannel, useLines } from "./queries";
import { readTarget, unseenThreadMention, usePageShown } from "./read-state";
import { MentionablesContext } from "./mention-picker";
import { ThreadPanel } from "./thread-panel";
import { Timeline } from "./timeline";
import { useAbilities } from "./use-abilities";
import { useChannelLive } from "./use-channel-live";
import { useOnline } from "./use-presence";
import { STATUS_LABELS } from "./views";
import { useInbox } from "./waiting-panel";

/** One chat that lives on a channel -- the reader's default chat, a chat
 *  shared in Lemma, a group mirrored from a platform, or a contact's -- full
 *  width, like any chat: everything said in it, oldest at the top, the box
 *  to say something, and beside it a thread or the chat's details. Kept live
 *  by the pod's socket while on screen, and marked read as far as the newest
 *  message the reader has seen. */
export function ChannelPage({ pod, channelId, visible, onNamed, onLeft }: {
    pod: Pod;
    channelId: string;
    visible: boolean;
    /** The page knows the chat's name before the tab does. */
    onNamed: (title: string) => void;
    /** The reader left the chat, or deleted it. */
    onLeft: () => void;
}) {
    const detail = useChannel(pod.id, channelId, visible);
    const channel = detail.data ?? null;
    const title = channel ? channelTitle(channel, pod.name) : null;
    useEffect(() => { if (title) onNamed(title); }, [title, onNamed]);

    if (detail.isPending) return <div className="cpage"><p className="cpage__quiet" role="status">Loading…</p></div>;
    /* A read that fails after one worked keeps the page it had. */
    if (!channel) {
        const gone = isMissing(detail.error);
        return (
            <div className="cpage">
                <div className="cpage__gone" role="alert">
                    <p>{gone ? "This chat is not here any more, or you are not in it." : isForbidden(detail.error) ? "You may not open this chat." : "Couldn’t load this chat."}</p>
                    <div className="cpage__gone-acts">
                        {!gone && !isForbidden(detail.error) && <button type="button" className="ghost-pill" onClick={() => void detail.refetch()}>Try again</button>}
                    </div>
                </div>
            </div>
        );
    }
    return <ChannelView key={channel.id} pod={pod} channel={channel} title={title ?? ""} visible={visible} onLeft={onLeft} />;
}

/** The people and agents in a channel as a row of faces: a few of them,
 *  and how many there are. One click opens the full list. */
function Faces({ members, me, online, open, onOpen }: { members: Member[]; me: string | null; online: ReadonlySet<string>; open: boolean; onOpen: () => void }) {
    const people = members.filter((one) => one.person.kind !== "AGENT");
    const agents = members.filter((one) => one.person.kind === "AGENT");
    const shown = [...people.slice(0, 3), ...agents.slice(0, 1)];
    const count = members.length;
    const said = count === 1 ? "1 in it" : count + " in it";
    return (
        <button type="button" className="cpage__faces" aria-label={"Who is in it: " + said} title={said} aria-expanded={open} onClick={onOpen}>
            <span className="cpage__face-stack">
                {shown.map((one) => {
                    const name = personName(one.person, me);
                    const here = one.person.kind === "MEMBER" && one.person.id ? online.has(one.person.id) : false;
                    return (
                        <span key={one.person.kind + (one.person.id ?? name)} className="cpage__face" data-online={here || undefined} title={name}>
                            <Avatar person={one.person} size={24} />
                        </span>
                    );
                })}
            </span>
            <span className="cpage__faces-count">{count}</span>
        </button>
    );
}

/** The top of a chat's history: what it is, who answers in it and how to
 *  ask -- the one place that says so, the way a chat app opens a channel
 *  with where it began. */
function ChatIntro({ pod, channel, title, me, onPeople }: { pod: Pod; channel: ChannelDetail; title: string; me: string | null; onPeople?: () => void }) {
    const teammate = pod.teammate?.name || pod.name;
    const places = channel.places.filter((place) => !place.pending);
    let said: string;
    if (channel.kind === "DIRECT") {
        said = "Your private line with " + teammate + ". " + answersHint(channel, teammate) + " Nobody else reads it.";
    } else if (channel.kind === "CONTACT") {
        const via = [...new Set(places.map((place) => platformName(place.platform)))];
        said = (channel.person?.name ?? (nameless(channel) === "A website visitor" ? "This visitor" : "This person")) + " is outside " + pod.name
            + (via.length ? " and writes by " + sayList(via) : "") + ". Your replies go back the same way.";
    } else if (places.length) {
        said = "This chat mirrors " + sayList(places.map(placeName)) + ". Messages there show up here, and what you send here goes back. " + answersHint(channel, teammate);
    } else {
        const who = sharedWith(channel.members, me);
        said = who === "Just you"
            ? "Just you so far. Add people to talk here together; anyone in it can type @ to ask " + teammate + "."
            : who + ", since " + new Date(channel.createdAt).toLocaleDateString([], { day: "numeric", month: "long" }) + ". " + answersHint(channel, teammate);
    }
    return (
        <div className="cintro" aria-label={"About " + title}>
            <p className="cintro__said">{said}</p>
            {onPeople && channel.kind === "GROUP" && (
                <div className="cintro__acts">
                    <button type="button" className="ghost-pill" onClick={onPeople}><PeopleIcon size={15} aria-hidden="true" /> Add people</button>
                </div>
            )}
        </div>
    );
}

/** A contact's chat, where it stands, in one sentence -- who is answering
 *  them -- and the one move that changes it: take over from the agent, or
 *  hand back to it. Who it is assigned to and its status sit beside it; the
 *  agent's own switch and the hand-back hours are in the chat's details. */
function ContactStrip({ pod, channel, may }: { pod: Pod; channel: ChannelDetail; may: { contactOps: boolean; write: boolean } }) {
    const me = useMe();
    const cache = useQueryClient();
    const move = useMutation({
        mutationFn: (assignee: "me" | null) => channelApi(pod.id).update(channel.id, { assignee }),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); refreshLists(cache, pod.id); },
    });
    const asks = channel.attention && !channel.assignee;
    const teammate = pod.teammate?.name || pod.name;
    const mine = Boolean(me) && channel.assignee?.id === me;
    const open = channel.status !== "closed";
    const state = asks
        ? "Needs a person" + (channel.attention?.reason ? ": " + channel.attention.reason : "") + " · " + sinceShort(channel.attention?.since)
        : channel.assignee
            ? (mine ? "You are answering." : (channel.assignee.name ?? "A member") + " is answering.")
                + (channel.agentsAnswer ? " " + teammate + " takes over after " + channel.handBackAfterHours + " quiet hours." : "")
            : !channel.agentsAnswer
                ? teammate + " is off here. Only people answer."
                : channel.status === "waiting_on_contact" ? "Waiting on their reply. " + teammate + " answers when they write."
                    : !open ? "Closed." : teammate + " is answering.";
    const act: { to: "me" | null; label: string } | null = !may.contactOps || !open
        ? null
        : !channel.assignee
            ? { to: "me", label: channel.agentsAnswer ? "Take over" : "I’ll answer" }
            : mine && channel.agentsAnswer ? { to: null, label: "Hand back to " + teammate } : null;
    return (
        <div className="cpage__strip" data-asks={asks || undefined}>
            <span className="cpage__strip-state">{state}</span>
            {may.contactOps && (
                <span className="cpage__strip-acts">
                    {act && (
                        <button type="button" className={act.to ? "pill-button cpage__take" : "ghost-pill cpage__take"} disabled={move.isPending} onClick={() => move.mutate(act.to)}>
                            {move.isPending ? "Saving…" : act.label}
                        </button>
                    )}
                    <ContactControls pod={pod} channel={channel} compact />
                </span>
            )}
            {!may.contactOps && channel.status && <span className="cpage__chip">{STATUS_LABELS[channel.status]}</span>}
            {move.isError && <span className="gside__problem" role="alert">Couldn’t change it.</span>}
        </div>
    );
}

function ChannelView({ pod, channel, title, visible, onLeft }: {
    pod: Pod;
    channel: ChannelDetail;
    title: string;
    visible: boolean;
    /** The reader left the channel, or deleted it. */
    onLeft: () => void;
}) {
    const me = useMe();
    const cache = useQueryClient();
    const timeline = useLines(pod.id, channel.id, visible);
    const live = useChannelLive(pod.id, channel, visible, me);
    const may = channelAbilities(useAbilities(pod.id), channel, pod.name);
    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const online = useOnline(pod.id, visible);
    const [side, setSide] = useState<"thread" | null>(null);
    const [details, setDetails] = useState<DetailsTab | null>(null);
    const [threadRoot, setThreadRoot] = useState<string | null>(null);
    /* Asked each time the reader opens a thread to reply in it: their next
       words go there, not into the channel's box. */
    const [replyAsked, setReplyAsked] = useState(0);
    const [pin, setPin] = useState(0);
    const [highlight, setHighlight] = useState<LineFocus | null>(null);
    /* Where the unread lines began when it was opened: the "New" mark
       stays there while the reader reads, rather than chasing them. */
    const [unreadFrom] = useState(channel.me.readSeq);
    const shown = usePageShown();
    const inbox = useInbox(pod.id, visible);

    /* The header's buttons open the details at their section; the same
       button again closes them. */
    const showSide = (at: DetailsTab) => setDetails(at);

    /* Opened at one line, now or while already open. */
    useEffect(() => {
        const take = () => {
            const asked = takeFocus(channel.id);
            if (!asked) return;
            setHighlight(asked);
            if (asked.threadRootId) { setThreadRoot(asked.threadRootId); setSide("thread"); setReplyAsked(0); }
        };
        if (visible) take();
        return onFocusAsked((id) => { if (id === channel.id) take(); });
    }, [channel.id, visible]);

    /* The line asked for may be older than the pages held: read back until
       it is there, a few pages at most. */
    const focusSeq = useRef(0);
    useEffect(() => {
        const wanted = highlight?.threadRootId ?? highlight?.lineId;
        if (!wanted || timeline.isPending || timeline.lines.some((line) => line.id === wanted)) return;
        if (timeline.hasNextPage && !timeline.isFetchingNextPage && focusSeq.current < 8) {
            focusSeq.current += 1;
            void timeline.fetchNextPage();
        }
    }, [highlight, timeline]);

    /* Read as far as the newest line seen; not while reading back, not
       behind another tab, and not past a reply naming the reader in a
       thread they have not opened. */
    const heldBack = unseenThreadMention(inbox.data?.mentioned ?? [], channel.id, side === "thread" ? threadRoot : null);
    const marked = useRef(0);
    const [progress, setProgress] = useState({ seenMax: 0, atEnd: true });
    const onProgress = useCallback((next: { seenMax: number; atEnd: boolean }) => {
        setProgress((was) => (was.seenMax === next.seenMax && was.atEnd === next.atEnd ? was : next));
    }, []);
    const { markRead } = live;
    useEffect(() => {
        /* The inbox says which thread mentions are unread; until it has,
           what reading to the end may mark is not known. */
        if (!visible || !shown || timeline.isPending || inbox.isPending) return;
        const target = readTarget({
            seenMax: progress.seenMax,
            atEnd: progress.atEnd,
            newestShown: newestSeq(timeline.lines) ?? 0,
            lastSeq: channel.lastSeq,
            readSeq: channel.me.readSeq,
            marked: marked.current,
            heldBack,
        });
        if (target === null) return;
        const was = marked.current;
        marked.current = target;
        markRead(target).catch(() => { marked.current = was; });
    }, [visible, shown, progress, timeline.isPending, timeline.lines, channel.lastSeq, channel.me.readSeq, markRead, heldBack, inbox.isPending]);

    /* A reply naming the reader is read in its thread, not the timeline:
       while that thread is open on screen, it is read to the newest line. */
    const openThreadNamesMe = side === "thread" && threadRoot !== null
        && (inbox.data?.mentioned ?? []).some((one) => one.channelId === channel.id && one.threadRootId === threadRoot);
    useEffect(() => {
        if (!visible || !shown || !openThreadNamesMe || !threadRoot) return;
        markRead(channel.lastSeq, threadRoot).catch(() => { /* read again when the inbox next says so */ });
    }, [visible, shown, openThreadNamesMe, threadRoot, channel.lastSeq, markRead]);

    const people = useMemo(
        () => mentionables(agents.data ?? [], channel.members.map((one) => one.person), me, channelReaders(channel, pod.members)),
        [agents.data, channel, pod.members, me],
    );
    const agentName = useCallback((agentId: string | null) => {
        const member = channel.members.find((one) => one.person.kind === "AGENT" && one.person.id === agentId);
        return member?.person.name ?? agents.data?.find((agent) => agent.id === agentId)?.label ?? pod.teammate?.name ?? "An agent";
    }, [channel.members, agents.data, pod.teammate]);
    const root = threadRoot ? timeline.lines.find((line) => line.id === threadRoot) ?? null : null;
    const posted = (line: Line) => {
        cache.setQueryData<InfiniteData<LinePage>>(linesKey(pod.id, channel.id), (was) => was ? { ...was, pages: upsertTimeline(was.pages, line).pages } : was);
        setPin((count) => count + 1);
        setHighlight(null);
        refreshLists(cache, pod.id);
    };
    const teammate = pod.teammate?.name || pod.name;
    /* Not the chat's name again -- the header says it -- but what the box
       does: in a shared chat, how to ask the teammate. */
    const placeholder = channel.kind === "DIRECT" ? "Message " + teammate
        : channel.kind === "CONTACT" ? "Write a reply"
        : "Message everyone, or type @ to ask " + teammate;
    const typingNames = typingIn(live.typing, null).map((one) => personName(one.who, me));
    const threadTyping = root ? typingIn(live.typing, root.id).map((one) => personName(one.who, me)) : [];

    /* What a screen reader hears: a new line from somebody else, and an
       answer starting -- never the answer a piece at a time. */
    const [said, setSaid] = useState("");
    useEffect(() => { if (live.announced) setSaid(live.announced); }, [live.announced]);
    const heardAnswering = useRef(live.answering);
    useEffect(() => {
        const started = answeringNews(heardAnswering.current, live.answering);
        heardAnswering.current = live.answering;
        if (started.length) setSaid(started.map((one) => agentName(one.agentId)).join(", ") + (started.length === 1 ? " is answering" : " are answering"));
    }, [live.answering, agentName]);

    const detailsOpen = details !== null;
    /* The teammate is in every shared chat, seated or not: its face is in
       the header's stack whichever. */
    const frontAgent = agents.data?.find((agent) => agent.front && agent.id) ?? null;
    const facesOf: Member[] = channel.kind === "GROUP" && frontAgent && !channel.members.some((one) => one.person.kind === "AGENT" && one.person.id === frontAgent.id)
        ? [...channel.members, { person: { kind: "AGENT", id: frontAgent.id, name: frontAgent.label }, role: "member", answers: "MENTIONED" }]
        : channel.members;
    const panelAt = details;
    const faces = useMemo(() => ({
        agentFace: (person: PersonRef, size: number) => {
            const agent = agents.data?.find((one) => one.id === person.id);
            return <AgentMark pod={pod} agent={agent ?? { name: person.id ?? "agent", label: person.name ?? "Agent", front: !person.id }} size={size} />;
        },
    }), [pod, agents.data]);
    return (
        /* Who the box a line is changed in can name: as the composer's. */
        <MentionablesContext.Provider value={people}>
        <ChatFaces.Provider value={faces}>
        <div className="cpage" data-side={side ?? undefined}>
            <div className="cpage__main">
                <header className="cpage__head">
                    <button type="button" className="cpage__name" aria-expanded={detailsOpen && panelAt === "about"} title="About this chat" onClick={() => showSide("about")}>
                        <span className="cpage__glyph" aria-hidden="true"><ChannelMark channel={channel} size={17} /></span>
                        <h1>{title}</h1>
                        <ChevronDownIcon size={14} aria-hidden="true" />
                    </button>
                    {channel.places.length > 0 && channel.kind !== "DIRECT" && (
                        <span className="cpage__places">
                            {channel.places.map((place) => (
                                <button key={place.id} type="button" className="cpage__chip" title={placeName(place) + (place.pending ? ", being created" : "")} aria-expanded={detailsOpen && panelAt === "connected"} onClick={() => showSide("connected")}>
                                    <ChannelIcon platform={place.platform} size={13} /> {place.title ?? placeName(place)}
                                </button>
                            ))}
                        </span>
                    )}
                    {channel.topic && <span className="cpage__topic" title={channel.topic}>{channel.topic}</span>}
                    <span className="cpage__spacer" />
                    {channel.kind !== "DIRECT" && <Faces members={facesOf} me={me} online={online} open={detailsOpen && panelAt === "members"} onOpen={() => showSide("members")} />}
                </header>
                {channel.kind === "CONTACT" && <ContactStrip pod={pod} channel={channel} may={may} />}
                {channel.archived && <p className="cpage__note" role="status">This chat is archived. Bring it back from Settings to write in it again.</p>}
                <Timeline
                    podId={pod.id}
                    channel={channel}
                    lines={timeline.lines}
                    loading={timeline.isPending}
                    failed={timeline.isError && !timeline.data}
                    hasOlder={Boolean(timeline.hasNextPage)}
                    loadingOlder={timeline.isFetchingNextPage}
                    onOlder={() => void timeline.fetchNextPage()}
                    onRetry={() => void timeline.refetch()}
                    onThread={(line) => { setThreadRoot(line.id); setSide("thread"); setReplyAsked((was) => was + 1); }}
                    pin={pin}
                    visible={visible}
                    mayWrite={may.write}
                    answering={live.answering}
                    agentName={agentName}
                    highlight={highlight ? highlight.threadRootId ?? highlight.lineId : null}
                    announced={said}
                    onProgress={onProgress}
                    unreadFrom={unreadFrom}
                    me={me}
                    intro={<ChatIntro pod={pod} channel={channel} title={title} me={me} onPeople={may.manage ? () => showSide("members") : undefined} />}
                />
                <p className="cpage__typing" aria-hidden={typingNames.length === 0}>{sayTyping(typingNames)}</p>
                <Composer
                    podId={pod.id}
                    channel={channel}
                    people={people}
                    placeholder={placeholder}
                    disabledReason={channel.archived ? "This chat is archived. Bring it back to write in it." : may.writeRefused}
                    onPosted={posted}
                    onTyping={() => live.typed(null)}
                />
            </div>
            {side === "thread" && root && (
                <ThreadPanel
                    key={root.id}
                    podId={pod.id}
                    channel={channel}
                    root={root}
                    people={people}
                    visible={visible}
                    mayWrite={may.write && !channel.archived}
                    writeRefused={may.writeRefused}
                    answering={live.answering}
                    agentName={agentName}
                    highlight={highlight?.threadRootId ? highlight.lineId : null}
                    typing={threadTyping}
                    replyAsked={replyAsked}
                    onTyping={live.typed}
                    onClose={() => { setSide(null); setThreadRoot(null); }}
                />
            )}
            {details && <ChatDetails pod={pod} channel={channel} may={may} at={details} onAt={setDetails} onClose={() => setDetails(null)} onLeft={onLeft} />}
        </div>
        </ChatFaces.Provider>
        </MentionablesContext.Provider>
    );
}
