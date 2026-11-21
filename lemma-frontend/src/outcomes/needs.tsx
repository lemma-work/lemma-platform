"use client";

import { useEffect, useState } from "react";
import type { Pod } from "@/data";
import { SkeletonRows } from "@/ui/skeleton";
import { Calm } from "./calm";
import type { Go, QueueItem, QueueKind } from "./model";
import { NeedRow, StillFace } from "./answer";
import { PageHead } from "./page-head";
import { Icon } from "./parts";

const KINDS: { kind: QueueKind; title: string; hint: string }[] = [
    { kind: "decide", title: "Decide", hint: "yes or no, from you" },
    { kind: "answer", title: "Answer", hint: "questions, and people waiting on a person" },
    { kind: "do", title: "Do", hint: "forms to fill in" },
];

/** Faces drawn over the empty page, overlapped. */
const FACES_SHOWN = 5;
/** How often the ages on the rows are said again. */
const TICK_MS = 60_000;

function useNow(every: number): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        const timer = window.setInterval(() => setNow(Date.now()), every);
        return () => window.clearInterval(timer);
    }, [every]);
    return now;
}

const lemmas = (count: number) => `${count} ${count === 1 ? "Lemma" : "Lemmas"}`;

/** The header's one line: how much waits and where, that it couldn't be
 *  read, or that nothing waits. Nothing while it is first read. */
export function needsLine({ pods, live, loading, failed }: {
    pods: readonly Pick<Pod, "id" | "name">[];
    live: readonly Pick<QueueItem, "podId" | "podName">[];
    loading: boolean;
    failed: boolean;
}): string | null {
    if (live.length === 0 && failed) return "What waits on you couldn’t be read just now.";
    if (live.length === 0 && loading) return null;
    if (live.length > 0) {
        const lemmasIn = new Set(live.map((item) => item.podId)).size;
        const total = Math.max(pods.length, lemmasIn);
        const things = `${live.length} ${live.length === 1 ? "thing" : "things"}`;
        if (total === 1) return `${things} in ${live[0].podName || pods[0]?.name || "your Lemma"}, oldest first.`;
        return `${things} across ${lemmasIn} of ${lemmas(total)}, oldest first.`;
    }
    if (pods.length === 0) return "Nothing waits on you.";
    if (pods.length === 1) return `Nothing waits on you in ${pods[0].name}.`;
    return `Nothing waits on you in your ${lemmas(pods.length)}.`;
}

/** Everything waiting on you, across every Lemma, oldest first, grouped by
 *  what it asks of you. Each row is answered in place where it can be (see
 *  `NeedRow`). A row answered here says what happened, then leaves at once,
 *  without waiting for the list to be read again. A read that failed says
 *  so: "Nothing needs you" over it would send someone away from a yes. */
export function NeedsYou({ pods, queue, loading, failed, onRetry, retrying = false, go }: {
    pods: readonly Pod[];
    queue: readonly QueueItem[];
    loading: boolean;
    failed: boolean;
    onRetry: () => void;
    /** A read is under way after Try again. */
    retrying?: boolean;
    go: Go;
}) {
    const now = useNow(TICK_MS);
    const [only, setOnly] = useState<QueueKind | "all">("all");
    /* Answered here and still saying so, kept drawn if the list read again
       has dropped them already; and those gone, by the wait they answered. */
    const [kept, setKept] = useState<ReadonlyMap<string, QueueItem>>(() => new Map());
    const [gone, setGone] = useState<ReadonlyMap<string, number>>(() => new Map());

    const listed = queue.filter((item) => gone.get(item.key) !== item.sinceMs);
    const live = listed.filter((item) => kept.get(item.key)?.sinceMs !== item.sinceMs);
    const leaving = [...kept.values()].filter((item) => !queue.some((one) => one.key === item.key));
    const rows = [...listed, ...leaving].sort((a, b) => a.sinceMs - b.sinceMs);
    const counts = Object.fromEntries(KINDS.map(({ kind }) => [kind, live.filter((item) => item.kind === kind).length])) as Record<QueueKind, number>;
    const showing = only !== "all" && rows.some((item) => item.kind === only) ? only : "all";
    const shown = showing === "all" ? rows : rows.filter((item) => item.kind === showing);

    const answered = (item: QueueItem) => setKept((was) => new Map(was).set(item.key, item));
    const left = (item: QueueItem) => {
        setKept((was) => {
            const next = new Map(was);
            next.delete(item.key);
            return next;
        });
        setGone((was) => new Map(was).set(item.key, item.sinceMs));
        /* The last of a kind gone: back to everything, rather than a filter
           that comes back on its own when something new of it arrives. */
        if (only !== "all" && !rows.some((one) => one.kind === only && one.key !== item.key)) setOnly("all");
    };

    const line = needsLine({ pods, live, loading, failed });
    let body;
    if (rows.length === 0 && failed) {
        body = (
            <Calm
                tone="warn"
                title="Couldn’t read what waits on you"
                actions={<button type="button" className="b b--m b--primary" disabled={retrying} onClick={onRetry}><Icon name="refresh" size={16} />{retrying ? "Trying…" : "Try again"}</button>}
            >
                <p>That isn’t the same as nothing waiting. Anything a Lemma asked is still in your chat with it.</p>
            </Calm>
        );
    } else if (rows.length === 0 && loading) {
        body = <SkeletonRows label="Reading what waits on you" rows={5} slow="Still reading what waits on you. It’s slower than usual." />;
    } else if (rows.length === 0) {
        const faces = pods.slice(0, FACES_SHOWN);
        body = (
            <Calm
                title="Nothing needs you"
                icon={faces.length === 0 ? "inbox" : undefined}
                art={faces.length > 0 ? <span className="nfaces" aria-hidden="true">{faces.map((pod) => <StillFace key={pod.id} pod={pod} size={48} />)}</span> : undefined}
            >
                <p>When a Lemma needs a yes, an answer or a form from you, it comes here and to your chat with it.</p>
            </Calm>
        );
    } else {
        body = (
            <>
                <div className="ph__tools" role="group" aria-label="Show">
                    <button type="button" className="kchip" aria-pressed={showing === "all"} onClick={() => setOnly("all")}>All<span>{live.length}</span></button>
                    {KINDS.filter(({ kind }) => counts[kind] > 0).map(({ kind, title }) => (
                        <button key={kind} type="button" className="kchip" aria-pressed={showing === kind} onClick={() => setOnly(kind)}>{title}<span>{counts[kind]}</span></button>
                    ))}
                </div>
                {KINDS.map(({ kind, title, hint }) => {
                    const items = shown.filter((item) => item.kind === kind);
                    if (items.length === 0) return null;
                    return (
                        <section key={kind} className="ngroup" aria-label={title}>
                            <h2 className="ngroup__h">{title}<span>{hint}</span></h2>
                            {items.map((item) => <NeedRow key={item.key} item={item} pods={pods} now={now} go={go} onAnswered={answered} onLeft={left} />)}
                        </section>
                    );
                })}
                <p className="nfoot">Answered here or where it was asked, it leaves this list either way.</p>
            </>
        );
    }

    return (
        <div className="ox-page nyou">
            <PageHead title="Needs you" line={line ?? undefined} />
            <div className="nyou__body">{body}</div>
        </div>
    );
}
