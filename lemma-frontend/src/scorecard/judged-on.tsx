"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { ArrowRightIcon } from "@/ui/icons";
import { alwaysChecked, isProposal, latestWeek, sayMet, type Measure, type WeekResult } from "./measures";
import { RowsButton } from "./sparkline";
import { nextReview, reviewPagePath, sayDate, sayReviewDay } from "./review";
import { addWord, kindWord, planFor, type Suggestion } from "./suggestions";
import {
    markSuggestion,
    readOpenSuggestions,
    readWeeks,
    reviewSchedule,
    suggestionsKey,
    useScorecard,
    useStandingWork,
    weeksKey,
} from "./store";

/** About's "Judged on": what the teammate is checked against, how the last
 *  week went, and what the review suggested.
 *
 *  The numbers are the review's, read back from `scorecard_weeks` as the
 *  `score_week` tool wrote them; this page counts nothing itself. A week is
 *  reliability, in design.md's words, not learning — so it is said as results
 *  against targets, never as the teammate getting better. */
export function JudgedOn({ pod, onSetup, onFile }: { pod: Pod; onSetup: () => void; onFile: (path: string) => void }) {
    const card = useScorecard(pod.id);
    const jobs = useStandingWork(pod.id);
    const review = reviewSchedule(jobs.data);
    const weeks = useQuery({ queryKey: weeksKey(pod.id), queryFn: () => readWeeks(pod.id), enabled: Boolean(review), staleTime: 60_000 });

    if (card.isPending || jobs.isPending) return <p className="aboutpage__quiet">Loading…</p>;
    if (card.isError) {
        return (
            <p className="aboutpage__quiet" role="alert">
                {isForbidden(card.error) ? "You may not read how " + pod.name + " is judged." : "Couldn’t load how " + pod.name + " is judged."}{" "}
                <button className="linkish" onClick={() => void card.refetch()}>Try again</button>
            </p>
        );
    }

    if (!card.data) {
        return (
            <p className="empty-row">
                Nothing to judge {pod.name} on yet.{" "}
                <button className="linkish" onClick={onSetup}>Choose how it’s judged</button>
            </p>
        );
    }

    const on = card.data.filter((measure) => measure.on);
    const latest = weeks.data ? latestWeek(weeks.data, card.data) : null;
    /* The checks every teammate gets are said once, under the list, the way
       the setup page says them — not as rows, and not in the count of what
       this teammate's own work met. */
    const sharedKeys = new Set(card.data.filter(alwaysChecked).map((one) => one.key));
    const ownResults = latest ? latest.results.filter((one) => !sharedKeys.has(one.key)) : [];
    const sharedResults = latest ? latest.results.filter((one) => sharedKeys.has(one.key)) : [];
    const sharedMissed = sharedResults.filter((one) => one.met === false);

    return (
        <div className="judged">
            {!review && (
                <p className="empty-row">
                    The Friday review is off, so nothing is counted yet.{" "}
                    <button className="linkish" onClick={onSetup}>Turn it on</button>
                </p>
            )}

            {review && latest ? (
                <>
                    <p className="judged__week">
                        Week to {sayDate(latest.week)} · {sayMet(ownResults)}{" "}
                        <button className="linkish" onClick={() => onFile(reviewPagePath(latest.week))}>Open the review <ArrowRightIcon size={13} /></button>
                    </p>
                    <ul className="judged__rows">
                        {ownResults.map((result) => (
                            <ResultRow key={result.key} podId={pod.id} result={result} measure={card.data?.find((one) => one.key === result.key)} />
                        ))}
                    </ul>
                    {sharedResults.length > 0 && (
                        <p className="judged__always" data-missed={sharedMissed.length > 0 || undefined}>
                            Always checked: {sharedResults.map((one) => one.measure.toLowerCase() + " — " + (one.shown || "nothing to count") + (one.met === false ? ", missed" : "")).join("; ")}.
                        </p>
                    )}
                </>
            ) : (
                <>
                    {review && <p className="judged__week">The first review is {sayReviewDay(nextReview(new Date()))}.</p>}
                    <ul className="judged__rows">
                        {on.filter((measure) => !alwaysChecked(measure) && !isProposal(measure)).map((measure) => (
                            <li key={measure.id}>
                                <span className="judged__what">{measure.measure}</span>
                                <span className="judged__target">{measure.targetLabel}</span>
                            </li>
                        ))}
                    </ul>
                </>
            )}

            {review && <Suggestions pod={pod} />}
        </div>
    );
}

function ResultRow({ podId, result, measure }: { podId: string; result: WeekResult; measure: Measure | undefined }) {
    const state = result.met === null ? "none" : result.met ? "met" : "missed";
    return (
        <li data-state={state}>
            <span className="judged__dot" aria-hidden="true" />
            <span className="judged__what">{result.measure}</span>
            <span className="judged__shown">
                {measure ? <RowsButton podId={podId} measure={measure} label={result.shown || "nothing to count"} end={result.week} /> : result.shown || "nothing to count"}
            </span>
            <span className="judged__target">
                {state === "met" ? "on target" : state === "missed" ? "target " + result.targetLabel : ""}
                <span className="sr-only">{state === "missed" ? ", missed" : ""}</span>
            </span>
        </li>
    );
}

/** What the review suggested, one tap each. Adding is done here, as the
 *  person pressing it, after `planFor` has checked the row; the review only
 *  ever wrote the row. */
function Suggestions({ pod }: { pod: Pod }) {
    const cache = useQueryClient();
    const open = useQuery({ queryKey: suggestionsKey(pod.id), queryFn: () => readOpenSuggestions(pod.id), staleTime: 60_000 });

    const settle = useMutation({
        mutationFn: async ({ suggestion, add }: { suggestion: Suggestion; add: boolean }) => {
            if (add) {
                const existing = suggestion.kind === "standing_work" ? null : await readText(pod.id, suggestion.path);
                const plan = planFor(suggestion, existing);
                if (plan.kind === "refuse") throw new Error(plan.reason);
                if (plan.kind === "write") {
                    await source.writeFile(pod.id, plan.path, plan.text);
                } else {
                    await source.createSchedule(pod.id, {
                        name: plan.name,
                        cron: plan.cron,
                        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                        target: "agent",
                        agentName: "pod_default",
                        workflowName: "",
                        instruction: plan.instruction,
                    });
                }
            }
            await markSuggestion(pod.id, suggestion.id, add ? "added" : "dismissed");
            return suggestion;
        },
        onSuccess: (suggestion) => {
            void cache.invalidateQueries({ queryKey: suggestionsKey(pod.id) });
            if (suggestion.kind === "remember") void cache.invalidateQueries({ queryKey: ["memory-notes", pod.id] });
            if (suggestion.kind === "skill") {
                void cache.invalidateQueries({ queryKey: ["skills", pod.id] });
                void cache.invalidateQueries({ queryKey: ["file", pod.id, suggestion.path] });
            }
            if (suggestion.kind === "standing_work") void cache.invalidateQueries({ queryKey: ["schedules", pod.id] });
        },
    });

    if (!open.isSuccess || open.data.length === 0) return null;
    const failed = settle.isError ? settle.variables?.suggestion.id : null;

    return (
        <section className="judged__suggest" aria-label="Suggested changes">
            <h3>Suggested changes</h3>
            <p className="aboutpage__quiet">From the review. Nothing changes until you add it.</p>
            <ul>
                {open.data.map((suggestion) => (
                    <li key={suggestion.id}>
                        <span className="judged__kind">{kindWord(suggestion.kind)}</span>
                        <span className="judged__said">
                            <span>{suggestion.title}</span>
                            {suggestion.why && <small>{suggestion.why}</small>}
                            {failed === suggestion.id && (
                                <small className="measure__error" role="alert">
                                    {settle.error instanceof Error ? settle.error.message : "That didn’t work."}
                                </small>
                            )}
                        </span>
                        <span className="judged__acts">
                            <button className="pill-button" disabled={settle.isPending} onClick={() => settle.mutate({ suggestion, add: true })}>{addWord(suggestion.kind)}</button>
                            <button className="ghost-pill" disabled={settle.isPending} onClick={() => settle.mutate({ suggestion, add: false })}>Not now</button>
                        </span>
                    </li>
                ))}
            </ul>
        </section>
    );
}

/** A text file's contents, or null when there is no file at the path. */
async function readText(podId: string, path: string): Promise<string | null> {
    try {
        const file = await source.readFile(podId, path);
        return file.text ?? "";
    } catch (error) {
        if ((error as { statusCode?: number } | null)?.statusCode === 404) return null;
        throw error;
    }
}
