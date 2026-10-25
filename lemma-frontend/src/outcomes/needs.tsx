"use client";

import { useState } from "react";
import type { Pod } from "@/data";
import { ageOf, viewOfItem, type Go, type QueueItem, type QueueKind } from "./model";
import { Empty, to } from "./parts";

const KINDS: { kind: QueueKind; title: string; hint: string }[] = [
    { kind: "decide", title: "Decide", hint: "Approvals and choices only a person can make" },
    { kind: "answer", title: "Answer", hint: "Questions a Lemma could not answer from what it knows, and people outside waiting on a person" },
    { kind: "do", title: "Do", hint: "Forms and steps waiting on you" },
];

/** Everything waiting on you, across every Lemma, oldest first. Each row
 *  says which Lemma it is for and how long it has waited, and opens where
 *  it is answered: its chat, or the workflow run whose form it is. */
export function NeedsYou({ pods, queue, loading, go }: { pods: readonly Pod[]; queue: readonly QueueItem[]; loading: boolean; go: Go }) {
    const [now] = useState(() => Date.now());
    const [only, setOnly] = useState<QueueKind | "all">("all");
    const counts = Object.fromEntries(KINDS.map(({ kind }) => [kind, queue.filter((item) => item.kind === kind).length])) as Record<QueueKind, number>;
    const shown = only === "all" ? queue : queue.filter((item) => item.kind === only);
    const outcomes = new Set(queue.map((item) => item.podId)).size;
    return (
        <div className="ox-page">
            <h1 className="ox-title">{queue.length === 0 ? "Nothing needs you" : `${queue.length} ${queue.length === 1 ? "thing needs" : "things need"} you`}</h1>
            <p className="ox-sub">{queue.length === 0 ? `Across ${pods.length} ${pods.length === 1 ? "Lemma" : "Lemmas"}. Anything new comes here, and to wherever you chat with them.` : `Across ${outcomes} of ${pods.length} Lemmas. Oldest first.`}</p>
            <div className="ox-chips" role="group" aria-label="Show">
                <button type="button" className="ox-chip" aria-pressed={only === "all"} onClick={() => setOnly("all")}>All<span>{queue.length}</span></button>
                {KINDS.map(({ kind, title }) => (
                    <button key={kind} type="button" className="ox-chip" aria-pressed={only === kind} onClick={() => setOnly(kind)} disabled={counts[kind] === 0}>{title}<span>{counts[kind]}</span></button>
                ))}
            </div>
            {loading && queue.length === 0 ? <p className="ox-loading">Gathering what’s waiting…</p> : shown.length === 0 ? (
                <Empty title="All clear here." />
            ) : (
                <div className="ox-queue">
                    {KINDS.filter(({ kind }) => only === "all" || only === kind).map(({ kind, title, hint }) => {
                        const items = shown.filter((item) => item.kind === kind);
                        if (items.length === 0) return null;
                        return (
                            <section key={kind} className="ox-queue__g">
                                <p className="ox-queue__h"><span>{title}</span><span className="ox-vs">{hint}</span></p>
                                {items.map((item) => (
                                    <a key={item.key} className="ox-item" {...to(viewOfItem(item), go)}>
                                        <span className="ox-item__t">
                                            <span className="ox-item__w">{item.title}</span>
                                            <span className="ox-item__o">{item.podName}{item.detail ? ` · ${item.detail}` : ""}</span>
                                        </span>
                                        <span className={"ox-item__age" + (now - item.sinceMs > 86_400_000 ? " ox-item__age--old" : "")}>{ageOf(item.sinceMs, now)}</span>
                                        <span className="ox-btn ox-btn--small">Open</span>
                                    </a>
                                ))}
                            </section>
                        );
                    })}
                </div>
            )}
            <p className="ox-vs ox-queue__foot">Each one opens its chat, or the workflow run it came from. Answer there, and it leaves this list.</p>
        </div>
    );
}
