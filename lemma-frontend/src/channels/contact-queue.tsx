"use client";

import { useMemo, useState } from "react";
import type { Pod } from "@/data";
import { useMe } from "@/session/use-me";
import { channelHref, goTo } from "@/shell/place-link";
import { ChannelMark } from "./chat-mark";
import { badge, channelTitle, personName, previewOf, sinceShort, type Channel, type ContactStatus } from "./model";
import { allChannels, useChannelList } from "./queries";
import { CONTACT_VIEWS, STATUS_LABELS, emptyView, filterFor, needsPerson, type ChannelView } from "./views";

/** The conversations with contacts, as a queue: who needs a person, who is
 *  yours, who nobody has taken, and all of them -- each row opening that
 *  contact's chat, where it is answered. */
export function ContactQueue({ pod, bare = false }: { pod: Pod; bare?: boolean }) {
    const me = useMe();
    const [view, setView] = useState<ChannelView>("needs");
    const [status, setStatus] = useState<ContactStatus | null>("open");
    const list = useChannelList(pod.id, filterFor(view, view === "needs" ? null : status), null);
    const rows = useMemo(() => allChannels(list.data), [list.data]);
    return (
        <section className="contacts__section cqueue" aria-labelledby="contacts-queue">
            <div className="cqueue__head">
                {bare ? <h2 id="contacts-queue" className="sr-only">Conversations</h2> : <h2 id="contacts-queue">Conversations</h2>}
                <div className="cqueue__views" role="group" aria-label="Which conversations">
                    {CONTACT_VIEWS.map((one) => (
                        <button key={one.view} type="button" className="cqueue__view" aria-pressed={view === one.view} onClick={() => setView(one.view)}>
                            {one.view === "contacts" ? "All" : one.label}
                        </button>
                    ))}
                </div>
                {view !== "needs" && (
                    <label className="cqueue__status">
                        <span className="sr-only">Status</span>
                        <select value={status ?? ""} onChange={(event) => setStatus((event.target.value || null) as ContactStatus | null)}>
                            <option value="">Any status</option>
                            {(Object.keys(STATUS_LABELS) as ContactStatus[]).map((one) => <option key={one} value={one}>{STATUS_LABELS[one]}</option>)}
                        </select>
                    </label>
                )}
            </div>
            {list.isPending ? (
                <p className="all__empty" role="status">Loading…</p>
            ) : rows.length === 0 ? (
                <p className="all__empty">{emptyView(view)}</p>
            ) : (
                <ul className="crows">
                    {rows.map((channel) => <QueueRow key={channel.id} pod={pod} channel={channel} me={me} />)}
                </ul>
            )}
            {list.hasNextPage && (
                <button type="button" className="ghost-pill cqueue__more" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                    {list.isFetchingNextPage ? "Loading…" : "Show more"}
                </button>
            )}
        </section>
    );
}

function QueueRow({ pod, channel, me }: { pod: Pod; channel: Channel; me: string | null }) {
    const title = channelTitle(channel, pod.name);
    const count = badge(channel.unread.lines);
    const asks = needsPerson(channel);
    const who = channel.assignee ? (channel.assignee.id === me ? "You are answering" : personName(channel.assignee, me) + " is answering") : null;
    return (
        <li className="crow cqueue__row" data-unread={channel.unread.lines > 0 || undefined}>
            <span className="cqueue__mark" aria-hidden="true"><ChannelMark channel={channel} /></span>
            <button type="button" className="crow__open" onClick={() => goTo(channelHref(pod.id, channel.id))}>
                <span className="crow__name">
                    {title}
                    {asks && <span className="cqueue__ask">Needs a person{channel.attention?.reason ? ": " + channel.attention.reason : ""}</span>}
                </span>
                <span className="crow__sub">{[who, previewOf(channel, me)].filter(Boolean).join(" · ")}</span>
            </button>
            <span className="cqueue__side">
                <span className="crow__when">{sinceShort(channel.lastMessageAt ?? channel.createdAt)}</span>
                {count && <span className="chats-page__unread">{count}</span>}
            </span>
        </li>
    );
}
