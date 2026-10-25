"use client";

import { useEffect, useMemo, useState } from "react";
import type { ConversationRef, Org, Pod } from "@/data";
import { key } from "@/session/storage";
import { ageOf, viewOfItem, workSince, type Go, type QueueItem } from "./model";
import { AppTiles, AskBox, useApps } from "./ask";
import { Empty, to } from "./parts";

const SEEN_KEY = key("outcomes-seen");
const DAY_MS = 86_400_000;
/** Apps on Home before "Show all": one row. */
const APPS_SHOWN = 5;
/** What waits on the reader, on Home, before the rest is a click away. */
const ASKS_SHOWN = 3;

/** When this person last looked at Home, read once, and written a few
 *  seconds after they arrive so a reload in passing does not reset it. */
function useLastSeen(): number | null {
    const [since] = useState<number | null>(() => {
        try {
            const raw = localStorage.getItem(SEEN_KEY);
            return raw ? Number(raw) : null;
        } catch {
            return null;
        }
    });
    useEffect(() => {
        const timer = window.setTimeout(() => {
            try { localStorage.setItem(SEEN_KEY, String(Date.now())); } catch { /* nowhere to keep it */ }
        }, 4000);
        return () => window.clearTimeout(timer);
    }, []);
    return since;
}

/** Every app across the Lemmas, newest first: what people use their work
 *  through. One row on Home, the rest a click away. */
function AllApps({ pods, go }: { pods: readonly Pod[]; go: Go }) {
    const { apps, pending } = useApps(pods);
    const [all, setAll] = useState(false);
    return (
        <section aria-label="Apps">
            <div className="ox-sec">
                <h2>Apps</h2>
                {apps.length > APPS_SHOWN && <button type="button" className="ox-link" onClick={() => setAll((was) => !was)}>{all ? "Show fewer" : `Show all ${apps.length}`}</button>}
            </div>
            {apps.length === 0
                ? <p className="ox-vs ox-apps__none">{pending ? "Loading apps…" : "No apps yet. Ask a Lemma to build one, and it shows here."}</p>
                : <AppTiles apps={all ? apps : apps.slice(0, APPS_SHOWN)} go={go} />}
        </section>
    );
}

/** Home: what needs the reader, said once; a box to ask any Lemma; what is
 *  waiting, a few lines of it; and the apps. The Lemmas' lanes are on
 *  Lemmas, and the chats are in the sidebar. */
export function Today({ org, pods, activity, queue, me, go, onAsk }: {
    org: Org;
    pods: readonly Pod[];
    activity: Map<string, ConversationRef[]>;
    queue: readonly QueueItem[];
    me: string;
    go: Go;
    onAsk: (podId: string, text: string) => void;
}) {
    const [now] = useState(() => Date.now());
    const lastSeen = useLastSeen();
    const all = useMemo(() => [...activity.values()].flat(), [activity]);
    const work = workSince(all, lastSeen ?? now - DAY_MS);
    const first = me === "You" ? null : me.split(" ")[0];
    const named = first ? `, ${first}` : "";
    const headline = queue.length > 0
        ? `${queue.length} ${queue.length === 1 ? "thing needs" : "things need"} you${named}.`
        : `Nothing needs you${named}.`;
    const sub = work.lemma > 0
        ? `Your Lemmas started ${work.lemma} ${work.lemma === 1 ? "run" : "runs"} on their own ${lastSeen ? "since you were last here" : "in the last day"}.`
        : null;

    if (pods.length === 0) {
        return (
            <div className="ox-page ox-home">
                <Empty title={`No Lemmas at ${org.name} yet.`}>
                    <p>Give one something to own, and it builds and runs what it takes.</p>
                    <a className="ox-btn ox-btn--primary" {...to({ name: "outcomes" }, go)}>Start a Lemma</a>
                </Empty>
            </div>
        );
    }

    return (
        <div className="ox-page ox-home">
            <h1 className="ox-home__t">{headline}</h1>
            {sub && <p className="ox-home__sub">{sub}</p>}
            <AskBox pods={pods} onAsk={onAsk} />
            {queue.length > 0 && (
                <section aria-label="Needs you">
                    <div className="ox-sec">
                        <h2>Needs you</h2>
                        {queue.length > ASKS_SHOWN && <a className="ox-link" {...to({ name: "needs" }, go)}>All {queue.length}</a>}
                    </div>
                    <div className="ox-queue">
                        {queue.slice(0, ASKS_SHOWN).map((item) => (
                            <a key={item.key} className="ox-item" {...to(viewOfItem(item), go)}>
                                <span className="ox-item__t">
                                    <span className="ox-item__w">{item.title}</span>
                                    <span className="ox-item__o">{item.podName}{item.detail ? ` · ${item.detail}` : ""}</span>
                                </span>
                                <span className={"ox-item__age" + (now - item.sinceMs > DAY_MS ? " ox-item__age--old" : "")}>{ageOf(item.sinceMs, now)}</span>
                                <span className="ox-btn ox-btn--small">{item.kind === "decide" ? "Decide" : item.kind === "answer" ? "Answer" : "Open"}</span>
                            </a>
                        ))}
                    </div>
                </section>
            )}
            <AllApps pods={pods} go={go} />
        </div>
    );
}
