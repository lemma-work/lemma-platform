"use client";

import { useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { ageOf, dailyActivity, formatMeasure, isRunning, outcomeView, readMeasure, standingOf, type Go, type Plan, type QueueItem } from "./model";
import { ActivityChart, to } from "./parts";
import { TeammateFace } from "@/space/teammate-face";

/** Each plan's measure, answered: the number on every lane that has one. */
function useMeasures(pods: readonly Pod[], plans: Map<string, Plan | null | undefined>): Map<string, number | null> {
    const results = useQueries({
        queries: pods.map((pod) => {
            const sql = plans.get(pod.id)?.measure ?? null;
            return {
                queryKey: ["outcome-measure", pod.id, sql],
                queryFn: async () => readMeasure((await source.runQuery(pod.id, sql as string)).items),
                enabled: Boolean(sql),
                staleTime: 5 * 60_000,
                retry: false,
            };
        }),
    });
    return new Map(pods.map((pod, index) => [pod.id, results[index]?.data ?? null]));
}

function ownerOf(pod: Pod): string | null {
    if (!pod.hiredBy) return null;
    const member = pod.members.find((one) => one.userId === pod.hiredBy);
    return member ? member.name : null;
}

function latestOf(conversations: readonly ConversationRef[]): number | null {
    const times = conversations.map((one) => (one.activityAt ? Date.parse(one.activityAt) : Number.NaN)).filter((at) => !Number.isNaN(at));
    return times.length > 0 ? Math.max(...times) : null;
}

function rightNow(conversations: readonly ConversationRef[], now: number): { live: boolean; text: string } {
    const running = conversations.filter(isRunning);
    if (running.length > 0) {
        const first = running[0].title;
        return { live: true, text: running.length > 1 ? `${first}, and ${running.length - 1} more` : first };
    }
    const latest = latestOf(conversations);
    return { live: false, text: latest ? `Last active ${ageOf(latest, now)} ago` : "Quiet so far" };
}

/** Every Lemma as a lane, the busiest first: its face and what it owns, two weeks of
 *  work a bar a day, its number against its target, what it is doing right
 *  now and what waits on you there. */
export function Board({ pods, activity, plans, queue, go }: {
    pods: readonly Pod[];
    activity: Map<string, ConversationRef[]>;
    plans: Map<string, Plan | null | undefined>;
    queue: readonly QueueItem[];
    go: Go;
}) {
    const [now] = useState(() => Date.now());
    const values = useMeasures(pods, plans);
    const lanes = pods
        .map((pod) => ({ pod, latest: latestOf(activity.get(pod.id) ?? []) }))
        .sort((a, b) => (b.latest ?? 0) - (a.latest ?? 0) || a.pod.name.localeCompare(b.pod.name));
    return (
        <>
            <section className="ox-board" aria-label="Lemmas">
                <div className="ox-board__head" aria-hidden="true"><span>Lemma</span><span>Last 2 weeks</span><span>Where it stands</span><span>Right now</span><span>Needs you</span></div>
                {lanes.map(({ pod }) => {
                    const plan = plans.get(pod.id);
                    const value = values.get(pod.id);
                    const standing = standingOf(plan, value);
                    const conversations = activity.get(pod.id) ?? [];
                    const live = rightNow(conversations, now);
                    const owner = ownerOf(pod);
                    const mine = queue.filter((item) => item.podId === pod.id).length;
                    const owns = plan?.outcome || "";
                    return (
                        <a key={pod.id} className="ox-lane" {...to(outcomeView(pod.id), go)}>
                            <span className="ox-lane__who">
                                <span className="ox-lane__name"><TeammateFace pod={pod} size={22} />{pod.name}{owner && <span className="ox-lane__owner">{owner}</span>}</span>
                                <span className={"ox-lane__owns" + (owns ? "" : " ox-lane__owns--none")}>{owns || pod.description || "Nothing it owns yet"}</span>
                            </span>
                            <span className="ox-lane__chart"><ActivityChart days={dailyActivity(conversations, new Date(now))} /></span>
                            <span className="ox-lane__num">
                                {value !== null && value !== undefined && plan
                                    ? <><span className="ox-big ox-num">{formatMeasure(value, plan.unit)}</span>{plan.target && <span className="ox-vs">target {plan.target}</span>}{standing && <span className={"ox-pill" + (standing === "behind" ? " ox-pill--behind" : "")}>{standing === "behind" ? "Behind" : "On track"}</span>}</>
                                    : <span className="ox-vs">{plan ? "No number yet" : "No plan yet"}</span>}
                            </span>
                            <span className={"ox-now" + (live.live ? " ox-now--live" : "")}>{live.live && <i aria-hidden="true" />}<span>{live.text}</span></span>
                            <span className="ox-wait">{mine > 0 ? <><span className="ox-count ox-count--quiet">{mine}</span>{mine === 1 ? "thing" : "things"}</> : <span className="ox-vs">Nothing</span>}</span>
                        </a>
                    );
                })}
            </section>
            <div className="ox-foot">
                <p className="ox-legend"><span><i className="ox-dot ox-dot--lemma" aria-hidden="true" />Started by the Lemma</span><span><i className="ox-dot ox-dot--person" aria-hidden="true" />Started by a person</span></p>
                <p>Counts are the conversations you can see with each Lemma.</p>
            </div>
        </>
    );
}
