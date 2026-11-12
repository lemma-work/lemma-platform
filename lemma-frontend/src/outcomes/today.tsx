"use client";

import { useMemo, useState } from "react";
import type { ConversationRef, Org, Pod } from "@/data";
import { sinceShort, type Channel } from "@/channels/model";
import { useMe } from "@/session/use-me";
import { chatItems, chatView, inSidebar, rowOfChat, type Ask, type Go, type QueueItem } from "./model";
import { NeedRow } from "./answer";
import { AskBox } from "./ask";
import { markOf, readsAhead } from "./sidebar";
import { Empty, to } from "./parts";

/** What waits on the reader, on Home, before the rest is a click away. */
const ASKS_SHOWN = 3;
/** Chats to pick up again, under the box. */
const RECENT_SHOWN = 5;

/** A greeting for the hour, the way an empty chat opens. */
function greeting(now: number): string {
    const hour = new Date(now).getHours();
    if (hour < 5 || hour >= 18) return "Good evening";
    if (hour < 12) return "Good morning";
    return "Good afternoon";
}

/** Home is a new chat: a greeting, or what needs the reader said once; the
 *  box, with who is asked at its foot; a few of the things waiting; and the
 *  chats to pick up again, across every Lemma -- the sidebar folds each
 *  Lemma to its name, so this is where the latest of them are. A failed
 *  read of what waits says so, rather than greeting over it. */
export function Today({ org, pods, activity, channels, queue, queueFailed, onRetryQueue, me, go, onAsk }: {
    org: Org;
    pods: readonly Pod[];
    activity: Map<string, ConversationRef[]>;
    /** The shared chats the sidebar has read: those of the Lemmas it is
     *  showing. Home does not read every Lemma's to list a few. */
    channels: ReadonlyMap<string, readonly Channel[]>;
    queue: readonly QueueItem[];
    queueFailed: boolean;
    onRetryQueue: () => void;
    me: string;
    go: Go;
    onAsk: Ask;
}) {
    const [now] = useState(() => Date.now());
    const reader = useMe();
    const recent = useMemo(() => {
        const { rest } = chatItems({ pods, conversations: activity, channels, me: reader });
        return rest.filter(inSidebar).slice(0, RECENT_SHOWN);
    }, [pods, activity, channels, reader]);
    const first = me === "You" ? null : me.split(" ")[0];
    const named = first ? `, ${first}` : "";
    const headline = queue.length > 0
        ? `${queue.length} ${queue.length === 1 ? "thing needs" : "things need"} you${named}.`
        : `${greeting(now)}${named}.`;

    if (pods.length === 0) {
        return (
            <div className="ox-page ox-home">
                <Empty title={`No Lemmas at ${org.name} yet.`}>
                    <p>Give one something to own, and it builds and runs what it takes.</p>
                    <a className="ox-btn ox-btn--primary" {...to({ name: "hire" }, go)}>Start a Lemma</a>
                </Empty>
            </div>
        );
    }

    return (
        <div className="ox-page ox-home">
            <h1 className="ox-home__t">{headline}</h1>
            <AskBox pods={pods} onAsk={onAsk} autoFocus />
            {queueFailed && queue.length === 0 && (
                <section aria-label="Needs you">
                    <div className="ox-sec"><h2>Needs you</h2></div>
                    <p className="ox-note" role="alert">
                        Couldn’t read what’s waiting on you. <button type="button" className="ox-textbtn" onClick={onRetryQueue}>Try again</button>
                    </p>
                </section>
            )}
            {queue.length > 0 && (
                <section aria-label="Needs you">
                    <div className="ox-sec">
                        <h2>Needs you</h2>
                        {queue.length > ASKS_SHOWN && <a className="ox-link" {...to({ name: "needs" }, go)}>All {queue.length}</a>}
                    </div>
                    <div className="ox-queue">
                        {queue.slice(0, ASKS_SHOWN).map((item) => <NeedRow key={item.key} item={item} pods={pods} now={now} go={go} />)}
                    </div>
                </section>
            )}
            {recent.length > 0 && (
                <section aria-label="Recent chats">
                    <div className="ox-sec"><h2>Recent</h2></div>
                    <div className="ox-recent">
                        {recent.map((item) => {
                            const row = rowOfChat(item);
                            return (
                                <a key={item.key} className="ox-recent__row" {...to(chatView(item.open), go)} {...readsAhead(chatView(item.open))}>
                                    <span className="ox-glyph">{markOf(row)}</span>
                                    <span className="ox-recent__t">{item.title}</span>
                                    <span className="ox-recent__k">{item.podName}</span>
                                    {item.running
                                        ? <i className="ox-sign ox-sign--live" role="img" aria-label="Running now" />
                                        : <span className="ox-recent__when">{item.at !== null ? sinceShort(new Date(item.at).toISOString()) : ""}</span>}
                                </a>
                            );
                        })}
                    </div>
                </section>
            )}
        </div>
    );
}
