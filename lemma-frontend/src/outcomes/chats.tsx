"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode, type RefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source, NEW_CONVERSATION, type ConversationRef, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { applyArchived, applyTitle, patchConversationLists, refreshConversationLists, titleToSend } from "@/thread/conversation-list";
import { LiveConversation } from "@/thread/live-conversation";
import { ConversationPane } from "@/thread/conversation";
import { ChannelPage } from "@/channels/channel-page";
import { NewSharedChat } from "@/channels/new-channel";
import { GroupSheets, type GroupSheet } from "@/channels/source-sheets";
import { groupStarters, platformName, type Starter } from "@/channels/sources";
import { mayStartChannel } from "@/channels/abilities";
import { useAbilities } from "@/channels/use-abilities";
import { allChannels, useChannelList } from "@/channels/queries";
import { filterFor, needsPerson } from "@/channels/views";
import { useInboxLive } from "@/channels/waiting-panel";
import type { Channel } from "@/channels/model";
import { ChannelIcon } from "@/shell/channels";
import { useSurfaces } from "@/shell/surfaces";
import { useMaySeeContacts } from "@/space/contact-queries";
import { NEW_CHAT, broughtSaid, chatView, outcomeView, type ChatOpen, type Fill, type Go, type Handoff, type OpenChat, type View } from "./model";
import { closeMenu } from "./lemma-bar";
import { pinKey, retitlePin, togglePin, unpin, usePins } from "./pins";
import type { Calls } from "./calls";
import { useVoiceConfigured } from "@/call/voice-config";
import { ConnectBanner } from "@/connect/pod-connect";
import { Empty, Icon, to } from "./parts";
import { useSetup } from "./data";
import type { FrameActions } from "./actions";
import { BesidePane, type Beside } from "./beside";
import { PaneDivider } from "@/shell/pane-divider";
import { clampPaneWidth } from "@/shell/split-tabs";
import { key as storageKey } from "@/session/storage";

const NOOP = () => undefined;

/** How much of a chat's width the conversation keeps when something is open
 *  beside it, as a percentage: the reader's, on this browser. */
const BESIDE_WIDTH_KEY = storageKey("outcomes-beside-width");

function storedBesideWidth(): number {
    try {
        const raw = localStorage.getItem(BESIDE_WIDTH_KEY);
        return clampPaneWidth(raw ? JSON.parse(raw) : 52);
    } catch {
        return 52;
    }
}

/* ── lists read for every Lemma ───────────────────────────────────────── */

type Keep<T> = (podId: string, list: readonly T[] | null) => void;

/** A list per Lemma, read by feeds that draw nothing -- one per Lemma, so
 *  each reads under the key the workspace keeps it under -- and handed up
 *  here to be merged. */
function useByPod<T>(): [ReadonlyMap<string, readonly T[]>, Keep<T>] {
    const [lists, setLists] = useState<ReadonlyMap<string, readonly T[]>>(() => new Map());
    const keep = useCallback<Keep<T>>((podId, list) => {
        setLists((was) => {
            if (list === null && !was.has(podId)) return was;
            const next = new Map(was);
            if (list) next.set(podId, list);
            else next.delete(podId);
            return next;
        });
    }, []);
    return [lists, keep];
}

/** One Lemma's chats on a channel. `live`, it holds the Lemma's `inbox`
 *  topic too, so new messages and waiting questions move its rows as they
 *  happen, as the workspace's do under its bell. */
function ChannelFeed({ podId, live, onList }: { podId: string; live: boolean; onList: Keep<Channel> }) {
    useInboxLive(podId, live);
    const list = useChannelList(podId, { archived: false });
    const data = list.data;
    useEffect(() => { onList(podId, data ? allChannels(data) : null); }, [podId, data, onList]);
    useEffect(() => () => onList(podId, null), [podId, onList]);
    return null;
}

/** Every Lemma's shared chats, by Lemma. Render the feeds it returns. One
 *  caller holds them `live`, the sidebar, which is always there; any other
 *  reads the same lists from the cache. */
export function useChannelsByPod(pods: readonly Pod[], { live = false }: { live?: boolean } = {}): [ReadonlyMap<string, readonly Channel[]>, ReactNode] {
    const [channels, keep] = useByPod<Channel>();
    return [channels, pods.map((pod) => <ChannelFeed key={pod.id} podId={pod.id} live={live} onList={keep} />)];
}

function WaitingFeed({ podId, onList }: { podId: string; onList: Keep<Channel> }) {
    const list = useChannelList(podId, filterFor("needs", null));
    const data = list.data;
    useEffect(() => { onList(podId, data ? allChannels(data).filter(needsPerson) : null); }, [podId, data, onList]);
    useEffect(() => () => onList(podId, null), [podId, onList]);
    return null;
}

function ContactsFeed({ podId, onList }: { podId: string; onList: Keep<Channel> }) {
    return useMaySeeContacts(podId) ? <WaitingFeed podId={podId} onList={onList} /> : null;
}

/** The contacts waiting on a person, by Lemma, for whoever may look after
 *  them there: what Needs you counts beside the questions and the forms.
 *  Render the feeds it returns. */
export function useContactsWaiting(pods: readonly Pod[]): [ReadonlyMap<string, readonly Channel[]>, ReactNode] {
    const [waiting, keep] = useByPod<Channel>();
    return [waiting, pods.map((pod) => <ContactsFeed key={pod.id} podId={pod.id} onList={keep} />)];
}

/* ── the main area ────────────────────────────────────────────────────── */

/** Whatever the sidebar opened, filling the main area: a chat with a Lemma
 *  on the workspace's own thread and composer, a shared chat on its own
 *  page, or a contact's chat. Nothing here leaves the frame. */
export function ChatPane({ pods, open, activity, go, actions, calls, handoff, onHandoffSent, fill, onFilled }: {
    pods: readonly Pod[];
    open: ChatOpen;
    activity: Map<string, ConversationRef[]>;
    go: Go;
    actions: FrameActions;
    calls: Calls;
    handoff: Handoff | null;
    onHandoffSent: () => void;
    fill: Fill | null;
    onFilled: () => void;
}) {
    if (open.kind === "none") {
        return (
            <div className="ox-chats__home">
                <p className="ox-chats__home-t">Pick a chat, or start one</p>
                <p className="ox-vs">Your chats and the ones you share with people are on the left, under the Lemma each is with.</p>
                <a className="ox-btn ox-btn--primary" {...to({ name: "today" }, go)}>New chat</a>
            </div>
        );
    }
    const pod = pods.find((one) => one.id === open.podId) ?? null;
    if (!pod) return <div className="ox-thread__none"><Empty title="This chat is with a Lemma you can’t open."><p>It may have been removed, or you were never added to it.</p></Empty></div>;
    if (open.kind === "chat") {
        const title = activity.get(pod.id)?.find((one) => one.id === open.id)?.title ?? null;
        return <ChatThread pod={pod} id={open.id} title={title} go={go} actions={actions} calls={calls} handoff={handoff} onHandoffSent={onHandoffSent} fill={fill} onFilled={onFilled} />;
    }
    if (open.kind === "shared") return <SharedThread key={open.id} pod={pod} id={open.id} go={go} />;
    return <ContactThread key={open.id} pod={pod} id={open.id} go={go} />;
}

/** The key the open conversation's pane is mounted under. It changes when
 *  the reader opens another conversation, and not when a new one is given
 *  its id, so a reply already streaming into it is not cut off. */
function usePaneKey(podId: string, id: string, adopted: RefObject<string | null>): string {
    const [held, setHeld] = useState({ podId, id, generation: 0 });
    if (held.podId !== podId || held.id !== id) {
        const same = held.podId === podId && held.id === NEW_CHAT && adopted.current === id;
        setHeld({ podId, id, generation: same ? held.generation : held.generation + 1 });
    }
    return `${podId}:${held.generation}`;
}

/** A conversation with a Lemma, on the workspace's own thread and composer:
 *  approvals, questions, files and a reply still streaming all work here as
 *  they do there. A chat not started yet opens on who it is with -- its
 *  apps, and ways to start one with people instead -- and the box at the
 *  bottom, which is where the Lemma is picked. */
function ChatThread({ pod, id, title, go, actions, calls, handoff, onHandoffSent, fill, onFilled }: {
    pod: Pod;
    id: string;
    title: string | null;
    go: Go;
    actions: FrameActions;
    calls: Calls;
    handoff: Handoff | null;
    onHandoffSent: () => void;
    fill: Fill | null;
    onFilled: () => void;
}) {
    const created = useRef<string | null>(null);
    const key = usePaneKey(pod.id, id, created);
    const fresh = id === NEW_CHAT;
    const filled = fill?.podId === pod.id ? fill : null;
    /* A new chat shows the thread once words are on their way into it, and
       keeps showing it while the first reply comes, until it has its id. */
    const pending = fresh && ((handoff !== null && handoff.podId === pod.id && !handoff.sent) || filled !== null);
    const [started, setStarted] = useState<string | null>(null);
    if (pending && started !== key) setStarted(key);
    const threaded = !fresh || pending || started === key;
    /* A new chat starts on the Lemma's own page, which is a box to ask it
       something. Arriving at one with no words on the way -- an old link, a
       bookmark -- is arriving there. */
    useEffect(() => {
        if (!threaded) go(outcomeView(pod.id), { replace: true });
    }, [threaded, go, pod.id]);
    /* Offered only once the server says it can place a call: a voice engine
       is a server setting. The sample pod has no server to ask. */
    const voiceReady = useVoiceConfigured(source.label === "sample" ? null : pod.id) === true;
    /* What the chat opened, beside it, for as long as this conversation is
       the one in the pane: another chat opens on its own. */
    const [held, setHeld] = useState<{ key: string; beside: Beside } | null>(null);
    const beside = held?.key === key ? held.beside : null;
    const open = useCallback((next: Beside) => setHeld({ key, beside: next }), [key]);
    const [width, setWidth] = useState(storedBesideWidth);
    useEffect(() => {
        try { localStorage.setItem(BESIDE_WIDTH_KEY, JSON.stringify(width)); } catch { /* kept for this visit only */ }
    }, [width]);
    const opens = {
        onOpenApp: (name: string) => open({ kind: "app", name }),
        onOpenFile: (path: string) => open({ kind: "file", path }),
        onOpenTable: (name: string) => open({ kind: "table", name }),
    };
    /* With something open beside it, the chat -- its header, the banner over
       it and the thread -- keeps a column of its own, and the pane runs the
       full height beside it under a bar of its own: one header to each, side
       by side, not one stacked over the other. */
    return (
        <div className={"ox-thread" + (beside ? " ox-thread--beside" : "")} style={beside ? { ["--split-position" as string]: width + "%" } : undefined}>
            <div className="ox-thread__main">
                <header className="ox-thread__head">
                    {fresh ? <p className="ox-thread__t">New chat</p> : <ChatHead pod={pod} open={{ kind: "chat", podId: pod.id, id }} title={title} go={go} onComputer={() => open({ kind: "computer" })} />}
                </header>
                <ConnectBanner key={pod.id} pod={pod} />
                <SetupStrip pod={pod} />
                {!threaded ? <p className="ox-loading">Opening {pod.name}…</p> : (
                    <div className="ox-thread__body">
                        <div className="split">
                            <div className="convo-host">
                                {source.label === "live" ? (
                                    <LiveConversation
                                        key={key}
                                        pod={pod}
                                        conversationId={fresh ? NEW_CONVERSATION : id}
                                        fill={filled}
                                        onFilled={onFilled}
                                        autoSend={fresh && handoff && handoff.podId === pod.id && !handoff.sent ? handoff : null}
                                        createWith={fresh && handoff && handoff.podId === pod.id ? handoff.createWith : undefined}
                                        folderChosen={fresh && handoff && handoff.podId === pod.id ? handoff.folder : undefined}
                                        onAutoSent={onHandoffSent}
                                        onCreated={(made) => {
                                            created.current = made;
                                            go(chatView({ kind: "chat", podId: pod.id, id: made }), { replace: true });
                                        }}
                                        onVoice={voiceReady ? () => calls.start(pod, fresh ? null : id) : undefined}
                                        callError={calls.error}
                                        callRefresh={fresh ? "" : calls.refreshFor(id)}
                                        {...opens}
                                    />
                                ) : (
                                    <ConversationPane key={pod.id + ":" + id} pod={pod} conversationId={fresh ? NEW_CONVERSATION : id} fill={filled} onFilled={onFilled} {...opens} />
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>
            {beside && <PaneDivider value={width} onChange={setWidth} />}
            {beside && <BesidePane pod={pod} conversationId={fresh ? null : id} beside={beside} onOpen={open} onClose={() => setHeld(null)} go={go} actions={actions} />}
        </div>
    );
}

/** Over a Lemma's first chat, while its template is coming in: what it
 *  brings, and that its agent starts once it is in. Gone once it is. */
function SetupStrip({ pod }: { pod: Pod }) {
    const setup = useSetup(pod.id);
    if (!setup) return null;
    const brought = broughtSaid((setup.plan?.steps ?? []).map((step) => step.kind));
    const status = setup.status as string;
    const said = status === "COMPLETED"
        ? `Brought in ${brought || "what it starts with"}.`
        : status === "FAILED"
            ? `Couldn’t bring in everything${setup.error ? `: ${setup.error}` : ""}. ${pod.name} starts with what is here.`
            : status === "AWAITING_CONFIRMATION"
                ? "What it brings is waiting for a person to confirm it."
                : `Bringing in ${brought || "its tables, skills and automations"}…`;
    return (
        <p className="ox-setup" role="status">
            <span className="ox-setup__t">Setting up {pod.name}</span>
            <span className="ox-setup__items">{said}</span>
        </p>
    );
}

/** A chat's name, and in a menu under it what can be done with the chat,
 *  the way a session's title carries its own: rename it, pin it to the top
 *  of the sidebar, open the Lemma it is with or the machine it works on, or
 *  put it away. */
function ChatHead({ pod, open, title, go, onComputer }: {
    pod: Pod;
    open: OpenChat;
    title: string | null;
    go: Go;
    /** The machine it works on, opened beside the chat. */
    onComputer: () => void;
}) {
    const cache = useQueryClient();
    const pinned = usePins().some((pin) => pin.key === pinKey(open));
    const [draft, setDraft] = useState<string | null>(null);
    const shown = title ?? "Chat";
    const sample = source.label === "sample";
    /* The frame's own list of the Lemma's chats, beside the workspace's. */
    const patch = (change: (list: ConversationRef[] | undefined) => ConversationRef[] | undefined) => {
        patchConversationLists(cache, pod.id, change);
        cache.setQueryData<ConversationRef[]>(["conversations", pod.id, "outcome-activity"], change);
    };
    const rename = async () => {
        const next = titleToSend(draft ?? "");
        setDraft(null);
        if (!next || next === title) return;
        patch((list) => applyTitle(list, open.id, next));
        retitlePin(open, next);
        try {
            if (!sample) await lemma(pod.id).conversations.update(open.id, { title: next }, { pod_id: pod.id });
        } catch {
            void refreshConversationLists(cache, pod.id);
        }
    };
    const archive = async () => {
        patch((list) => applyArchived(list, open.id));
        unpin(open);
        go({ name: "today" });
        try {
            if (!sample) await lemma(pod.id).conversations.update(open.id, { is_archived: true }, { pod_id: pod.id });
        } catch {
            void refreshConversationLists(cache, pod.id);
        }
    };
    const item = (view: View) => {
        const link = to(view, go);
        return { href: link.href, onClick: (event: MouseEvent<HTMLAnchorElement>) => { closeMenu(event); link.onClick(event); } };
    };
    if (draft !== null) {
        return (
            <input
                className="ox-chathead__rename"
                aria-label="Chat name"
                value={draft}
                maxLength={200}
                autoFocus
                onFocus={(event) => event.currentTarget.select()}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={() => void rename()}
                onKeyDown={(event) => {
                    if (event.key === "Enter") { event.preventDefault(); void rename(); }
                    if (event.key === "Escape") { event.preventDefault(); setDraft(null); }
                }}
            />
        );
    }
    return (
        <details className="ox-more ox-chathead">
            <summary aria-label={`${shown}, with ${pod.name}`}>
                <span className="ox-chathead__t">{shown}</span>
                <Icon name="chevron" size={13} />
            </summary>
            <div className="ox-more__menu" role="menu">
                <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); setDraft(title ?? ""); }}>Rename</button>
                <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); togglePin(open, shown); }}>{pinned ? "Unpin" : "Pin to the top"}</button>
                <a role="menuitem" {...item(outcomeView(pod.id))}>Open {pod.name}</a>
                {/* The machine this chat works on, from its working folder. */}
                {source.label === "live" && <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); onComputer(); }}>Its computer</button>}
                <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); void archive(); }}>Archive</button>
            </div>
        </details>
    );
}

/** Ways to start a chat with people instead: a group made here, or the
 *  Lemma brought into a group on a platform it is connected to. Platforms
 *  it is not connected to are one link to connect them, not a row of dead
 *  options. */
export function StartWith({ pod, go, onConnect }: { pod: Pod; go: Go; onConnect: () => void }) {
    const mayShare = mayStartChannel(useAbilities(pod.id));
    const surfaces = useSurfaces(pod.id);
    const starters = useMemo(() => groupStarters(surfaces.data ?? []), [surfaces.data]);
    const [sheet, setSheet] = useState<GroupSheet | null>(null);
    const [making, setMaking] = useState(false);
    const openShared = (channelId: string) => go(chatView({ kind: "shared", podId: pod.id, id: channelId }));
    const start = (starter: Starter) => {
        if (!starter.surface) return;
        if (starter.platform === "WHATSAPP") setSheet({ kind: "whatsapp", surface: starter.surface });
        else if (starter.platform === "SLACK") setSheet({ kind: "slack", surface: starter.surface });
        else setSheet({ kind: "telegram", surface: starter.surface });
    };
    const ready = starters.filter((starter) => starter.state === "ready");
    const unconnected = starters.filter((starter) => starter.state !== "ready");
    if (!mayShare && ready.length === 0 && unconnected.length === 0) return null;
    return (
        <div className="ox-startwith">
            <span className="ox-startwith__t">Or with people:</span>
            {mayShare && (
                <button type="button" className="ox-startwith__b" onClick={() => setMaking(true)}>
                    <Icon name="people" size={14} />Group chat
                </button>
            )}
            {ready.map((starter) => (
                <button key={starter.platform} type="button" className="ox-startwith__b" onClick={() => start(starter)}>
                    <ChannelIcon platform={starter.platform} size={14} />{platformName(starter.platform)} {starter.platform === "SLACK" ? "channel" : "group"}
                </button>
            ))}
            {unconnected.length > 0 && (
                <button type="button" className="ox-startwith__more" onClick={onConnect} title={`Connect ${unconnected.map((one) => platformName(one.platform)).join(", ")}`}>
                    Connect {unconnected.length === 1 ? platformName(unconnected[0].platform) : "more"}
                </button>
            )}
            {sheet && <GroupSheets pod={pod} sheet={sheet} onClose={() => setSheet(null)} onOpenChannel={openShared} />}
            {making && <NewSharedChat pod={pod} onClose={() => setMaking(false)} onMade={(channelId) => { setMaking(false); openShared(channelId); }} />}
        </div>
    );
}

/** A chat shared with people, or someone's private line: the workspace's
 *  own channel page -- the timeline, threads, who is in it, its composer.
 *  Its header is the chat's one header, with the pin at its end. */
function SharedThread({ pod, id, go }: { pod: Pod; id: string; go: Go }) {
    const leave = useCallback(() => go(outcomeView(pod.id)), [go, pod.id]);
    const [title, setTitle] = useState<string | null>(null);
    return (
        <div className="ox-thread">
            <div className="ox-thread__body">
                <div className="pane channel-pane">
                    <ChannelPage pod={pod} channelId={id} visible onNamed={setTitle} onLeft={leave} actions={title && <PinButton open={{ kind: "shared", podId: pod.id, id }} title={title} />} />
                </div>
            </div>
        </div>
    );
}

/** Pin a shared chat to the top of the sidebar, or unpin it. */
function PinButton({ open, title }: { open: OpenChat; title: string }) {
    const pinned = usePins().some((pin) => pin.key === pinKey(open));
    return (
        <button type="button" className="ox-pinbtn" aria-pressed={pinned} aria-label={pinned ? "Unpin" : "Pin to the top"} title={pinned ? "Unpin" : "Pin to the top"} onClick={() => togglePin(open, title)}>
            <Icon name="pin" size={16} />
        </button>
    );
}

/* ── contacts ─────────────────────────────────────────────────────────── */

/** One contact's chat, with the way back to the Lemma's contacts. */
function ContactThread({ pod, id, go }: { pod: Pod; id: string; go: Go }) {
    const queue = outcomeView(pod.id, "contacts");
    const leave = useCallback(() => go(outcomeView(pod.id, "contacts")), [go, pod.id]);
    return (
        <div className="ox-thread">
            <header className="ox-thread__head ox-thread__head--slim">
                <a className="ox-thread__crumb" {...to(queue, go)}><Icon name="back" size={14} />{pod.name} contacts</a>
            </header>
            <div className="ox-thread__body">
                <div className="pane channel-pane">
                    <ChannelPage pod={pod} channelId={id} visible onNamed={NOOP} onLeft={leave} />
                </div>
            </div>
        </div>
    );
}
