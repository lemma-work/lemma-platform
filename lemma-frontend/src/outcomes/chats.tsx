"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, NEW_CONVERSATION, type ConversationRef, type Pod } from "@/data";
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
import type { Channel } from "@/channels/model";
import { channelHref } from "@/shell/place-link";
import { ChannelIcon } from "@/shell/channels";
import { useSurfaces } from "@/shell/surfaces";
import { useMaySeeContacts } from "@/space/contact-queries";
import { TeammateFace } from "@/space/teammate-face";
import { NEW_CHAT, chatView, outcomeView, type ChatOpen, type Fill, type Go, type Handoff } from "./model";
import { classic, readPlan } from "./data";
import { AskBox } from "./ask";
import { Empty, Icon, to } from "./parts";
import { WhatItRuns } from "./runs";
import type { FrameActions } from "./actions";

const NOOP = () => undefined;

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

function ChannelFeed({ podId, onList }: { podId: string; onList: Keep<Channel> }) {
    const list = useChannelList(podId, { archived: false });
    const data = list.data;
    useEffect(() => { onList(podId, data ? allChannels(data) : null); }, [podId, data, onList]);
    useEffect(() => () => onList(podId, null), [podId, onList]);
    return null;
}

/** Every Lemma's shared chats, by Lemma. Render the feeds it returns. */
export function useChannelsByPod(pods: readonly Pod[]): [ReadonlyMap<string, readonly Channel[]>, ReactNode] {
    const [channels, keep] = useByPod<Channel>();
    return [channels, pods.map((pod) => <ChannelFeed key={pod.id} podId={pod.id} onList={keep} />)];
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
export function ChatPane({ pods, open, activity, go, onAsk, actions, handoff, onHandoffSent, fill, onFilled }: {
    pods: readonly Pod[];
    open: ChatOpen;
    activity: Map<string, ConversationRef[]>;
    go: Go;
    /** Start a new chat with these words, here. */
    onAsk: (podId: string, text: string) => void;
    actions: FrameActions;
    handoff: Handoff | null;
    onHandoffSent: () => void;
    fill: Fill | null;
    onFilled: () => void;
}) {
    if (open.kind === "none") {
        const first = pods[0];
        return (
            <div className="ox-chats__home">
                <p className="ox-chats__home-t">Pick a chat, or start one</p>
                <p className="ox-vs">Your chats and the ones you share with people are on the left, under the Lemma each is with.</p>
                {first && <a className="ox-btn ox-btn--primary" {...to(chatView({ kind: "chat", podId: first.id, id: NEW_CHAT }), go)}>New chat</a>}
            </div>
        );
    }
    const pod = pods.find((one) => one.id === open.podId) ?? null;
    if (!pod) return <div className="ox-thread__none"><Empty title="This chat is with a Lemma you can’t open."><p>It may have been removed, or you were never added to it.</p></Empty></div>;
    if (open.kind === "chat") {
        const title = activity.get(pod.id)?.find((one) => one.id === open.id)?.title ?? null;
        return <ChatThread pod={pod} id={open.id} title={title} pods={pods} go={go} onAsk={onAsk} actions={actions} handoff={handoff} onHandoffSent={onHandoffSent} fill={fill} onFilled={onFilled} />;
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
function ChatThread({ pod, id, title, pods, go, onAsk, actions, handoff, onHandoffSent, fill, onFilled }: {
    pod: Pod;
    id: string;
    title: string | null;
    pods: readonly Pod[];
    go: Go;
    onAsk: (podId: string, text: string) => void;
    actions: FrameActions;
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
    const opens = {
        onOpenApp: (name: string) => go({ name: "app", podId: pod.id, app: name }),
        onOpenFile: (path: string) => go({ name: "file", podId: pod.id, path }),
        onOpenTable: (name: string) => go({ name: "table", podId: pod.id, table: name }),
    };
    return (
        <div className="ox-thread">
            <header className="ox-thread__head">
                <p className="ox-thread__t">{fresh ? "New chat" : title ?? "Chat"}</p>
                <a className="ox-tag ox-tag--link" {...to(outcomeView(pod.id), go)}>{pod.name}</a>
                {!fresh && <a className="ox-thread__out" href={classic.conversation(pod.id, id)}>Open in the workspace</a>}
            </header>
            {!threaded ? <NewChat pod={pod} pods={pods} go={go} onAsk={onAsk} actions={actions} /> : (
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
                                    onAutoSent={onHandoffSent}
                                    onCreated={(made) => {
                                        created.current = made;
                                        go(chatView({ kind: "chat", podId: pod.id, id: made }), { replace: true });
                                    }}
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
    );
}

/** A chat not started yet: who it is with and what it owns; what it runs
 *  -- its apps, tables and automations -- each a click away, with a way to
 *  ask for a new one; ways to start a chat with people instead; and the box,
 *  with which Lemma at its foot. */
function NewChat({ pod, pods, go, onAsk, actions }: { pod: Pod; pods: readonly Pod[]; go: Go; onAsk: (podId: string, text: string) => void; actions: FrameActions }) {
    const plan = useQuery({ queryKey: ["outcome-plan", pod.id], queryFn: () => readPlan(pod.id), staleTime: 5 * 60_000 });
    const owns = plan.data?.outcome || "";
    const [seed, setSeed] = useState<{ text: string; id: number } | null>(null);
    const ask = (text: string) => setSeed((was) => ({ text, id: (was?.id ?? 0) + 1 }));
    return (
        <div className="ox-newchat">
            <div className="ox-newchat__body">
                <div className="ox-newchat__who">
                    <TeammateFace pod={pod} size={44} />
                    <div className="ox-newchat__id">
                        <p className="ox-newchat__name">{pod.name}</p>
                        <p className="ox-newchat__owns">{owns ? `Owns: ${owns}` : pod.description || "Nothing it owns yet."}</p>
                    </div>
                </div>
                <WhatItRuns pod={pod} go={go} onNew={ask} />
            </div>
            <div className="ox-newchat__foot">
                <StartWith pod={pod} go={go} onConnect={() => actions.connect(pod.id)} />
                <AskBox
                    pods={pods}
                    podId={pod.id}
                    onPod={(next) => go(chatView({ kind: "chat", podId: next, id: NEW_CHAT }), { replace: true })}
                    onAsk={onAsk}
                    autoFocus
                    seed={seed}
                    placeholder={`Ask ${pod.name} anything, or say what to do`}
                />
            </div>
        </div>
    );
}

/** Ways to start a chat with people instead: a group made here, or the
 *  Lemma brought into a group on a platform it is connected to. Platforms
 *  it is not connected to are one link to connect them, not a row of dead
 *  options. */
function StartWith({ pod, go, onConnect }: { pod: Pod; go: Go; onConnect: () => void }) {
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
 *  own channel page -- the timeline, threads, who is in it, its composer. */
function SharedThread({ pod, id, go }: { pod: Pod; id: string; go: Go }) {
    const leave = useCallback(() => go(outcomeView(pod.id)), [go, pod.id]);
    return (
        <div className="ox-thread">
            <header className="ox-thread__head ox-thread__head--slim">
                <a className="ox-tag ox-tag--link" {...to(outcomeView(pod.id), go)}>{pod.name}</a>
                <a className="ox-thread__out" href={channelHref(pod.id, id)}>Open in the workspace</a>
            </header>
            <div className="ox-thread__body">
                <div className="pane channel-pane">
                    <ChannelPage pod={pod} channelId={id} visible onNamed={NOOP} onLeft={leave} />
                </div>
            </div>
        </div>
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
                <a className="ox-thread__out" href={channelHref(pod.id, id)}>Open in the workspace</a>
            </header>
            <div className="ox-thread__body">
                <div className="pane channel-pane">
                    <ChannelPage pod={pod} channelId={id} visible onNamed={NOOP} onLeft={leave} />
                </div>
            </div>
        </div>
    );
}
