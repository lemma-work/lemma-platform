"use client";

import { useMemo, useState } from "react";
import { source, type Org, type Pod } from "@/data";
import { TeammateFace } from "@/space/teammate-face";
import { HISTORY_DAYS, useRecents } from "./data";
import { historyDays, historyRows, historySaid, outcomeView, type Go, type HistoryRow } from "./model";
import { Empty, to } from "./parts";

type Showing = "all" | "chats" | "ran" | "wrong";

const SHOWING: readonly { id: Showing; label: string }[] = [
    { id: "all", label: "Everything" },
    { id: "chats", label: "Chats" },
    { id: "ran", label: "Ran by itself" },
    { id: "wrong", label: "Went wrong" },
];

function shows(row: HistoryRow, showing: Showing): boolean {
    if (showing === "chats") return row.kind === "chat";
    if (showing === "ran") return row.kind !== "chat";
    if (showing === "wrong") return row.tone === "bad";
    return true;
}

function clock(at: number): string {
    return new Date(at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** What the reader's Lemmas have been doing, newest first, a day at a time:
 *  their chats with each, and the schedules and workflows that ran, each said
 *  once with how it ended and how many times. Every Lemma, or the one the
 *  address names. It lists what ran, as far as the reader may see it -- not
 *  each thing a Lemma sent or changed, which nothing records yet. */
export function History({ org, pods, podId, go }: {
    org: Org;
    pods: readonly Pod[];
    /** The one Lemma to show, or null for all of them. */
    podId: string | null;
    go: Go;
}) {
    const recents = useRecents(org.id);
    const [showing, setShowing] = useState<Showing>("all");
    const [now] = useState(() => Date.now());
    const faces = useMemo(() => new Map(pods.map((pod) => [pod.id, pod])), [pods]);
    const all = useMemo(() => historyRows(recents.data?.pods ?? []), [recents.data]);
    const mine = podId ? all.filter((row) => row.podId === podId) : all;
    const rows = mine.filter((row) => shows(row, showing));
    const days = historyDays(rows, now);
    const one = podId ? faces.get(podId) ?? null : null;
    /* Every Lemma the read answered for, quiet ones included, so the menu
       lists a Lemma before anything has moved in it. */
    const listed = (recents.data?.pods ?? []).map((pod) => ({ id: pod.pod_id, name: pod.pod_name })).sort((a, b) => a.name.localeCompare(b.name));

    return (
        <div className="ox-page ox-hist">
            <h1 className="ox-title">{one ? `What ${one.name} has been doing` : "History"}</h1>
            <p className="ox-sub">
                {source.label !== "live" ? "History needs a signed-in session."
                    : recents.isPending ? "Reading what has moved…"
                    : recents.isError ? "Couldn’t read what has moved" + (recents.error instanceof Error && recents.error.message ? `: ${recents.error.message}` : ".")
                    : historySaid(mine, HISTORY_DAYS) + (one ? "" : " Across the Lemmas you’re in.")}
            </p>
            {recents.isError && <p className="ox-hist__again"><button type="button" className="ox-btn" onClick={() => void recents.refetch()}>Try again</button></p>}
            {recents.isSuccess && (
                <>
                    <div className="ox-hist__bar">
                        <label className="ox-hist__pick">
                            <span className="ox-sr">Which Lemma</span>
                            <select value={podId ?? ""} onChange={(event) => go({ name: "history", podId: event.target.value || null })}>
                                <option value="">Every Lemma</option>
                                {listed.map((pod) => <option key={pod.id} value={pod.id}>{pod.name}</option>)}
                            </select>
                        </label>
                        <div className="ox-hist__show" role="group" aria-label="What to show">
                            {SHOWING.map((each) => (
                                <button key={each.id} type="button" className="ox-hist__chip" aria-pressed={showing === each.id} onClick={() => setShowing(each.id)}>{each.label}</button>
                            ))}
                        </div>
                    </div>
                    {rows.length === 0 ? (
                        <Empty title={mine.length === 0 ? "Nothing has moved yet." : "Nothing like that."}>
                            <p>{mine.length === 0 ? "Ask a Lemma something, or let one run on a schedule, and it shows here." : "Try showing everything."}</p>
                        </Empty>
                    ) : days.map((day) => (
                        <section key={day.label} aria-label={day.label}>
                            <div className="ox-sec"><h2>{day.label}</h2></div>
                            <div className="ox-hist__rows">
                                {day.rows.map((row) => {
                                    const pod = faces.get(row.podId);
                                    return (
                                        <a key={row.key} className="ox-hist__row" {...to(row.view, go)}>
                                            <span className="ox-hist__who">
                                                <TeammateFace pod={pod ?? { id: row.podId, name: row.podName, iconUrl: null }} size={22} />
                                                {!one && <span className="ox-hist__name">{row.podName}</span>}
                                            </span>
                                            <span className="ox-hist__t">{row.title}</span>
                                            <span className="ox-hist__what">
                                                {row.what}{row.count > 1 ? `, ${row.count} times` : ""}
                                            </span>
                                            <span className={"ox-hist__said" + (row.tone ? " ox-hist__said--" + row.tone : "")}>{row.said}</span>
                                            <span className="ox-hist__when">{clock(row.at)}</span>
                                        </a>
                                    );
                                })}
                            </div>
                        </section>
                    ))}
                    {one && <p className="ox-hist__again"><a className="ox-link" {...to(outcomeView(one.id), go)}>Back to {one.name}</a></p>}
                </>
            )}
        </div>
    );
}
