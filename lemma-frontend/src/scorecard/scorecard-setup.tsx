"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { lemma } from "@/session/client";
import { isForbidden } from "@/session/auth-state";
import { createRequest } from "@/schedule/schedules";
import { ArrowRightIcon, CheckIcon, LockIcon, SendIcon } from "@/ui/icons";
import { alwaysChecked, isProposal, whyOff, type Measure } from "./measures";
import type { MeasureHistory } from "./history";
import { REVIEW_CRON, REVIEW_SCHEDULE, nextReview, reviewInstruction, sayReviewDay } from "./review";
import { fixMeasure, unitsOf, wordsToMeasure } from "./units";
import { Sparkline, WeekNumber } from "./sparkline";
import {
    dropMeasure,
    ensureSuggestionsTable,
    previewKey,
    reviewSchedule,
    scorecardKey,
    startScorecard,
    updateMeasure,
    usePreview,
    useScorecard,
    useStandingWork,
} from "./store";

/** "How will you know {name} is doing well?" — the step after the hire.
 *
 *  The page starts from what the teammate makes, because that is what makes
 *  one scorecard different from the next: a support desk's conversations, a
 *  channel's reels, a factory's pull requests. Each measure is a test over
 *  those rows of work, shown with its last four weeks as the server counted
 *  them, so nothing is kept before it has been seen to count. A sentence in
 *  the person's own words goes to the teammate, which drafts a measure and
 *  checks it the same way; the draft comes back here as a proposal.
 *
 *  The checks every teammate gets — standing work on time, nothing left
 *  waiting — are one line, not rows. Keeping turns on the Friday review, run
 *  as whoever pressed it, which is what makes them the reviewer. */
export function ScorecardSetup({ pod, onAbout, onAskFor }: { pod: Pod; onAbout: () => void; onAskFor: (text: string) => void }) {
    const card = useScorecard(pod.id);
    const jobs = useStandingWork(pod.id);
    const preview = usePreview(pod.id, Boolean(card.data && card.data.length > 0));
    const cache = useQueryClient();
    const review = reviewSchedule(jobs.data);
    const me = useQuery({ queryKey: ["current-user"], queryFn: () => lemma().users.current(), staleTime: 5 * 60_000 });
    /* The review runs as whoever turned it on: the schedule's owner. */
    const mine = Boolean(review && me.data?.id && review.ownerId === me.data.id);
    const [words, setWords] = useState("");

    const refresh = () => {
        void cache.invalidateQueries({ queryKey: scorecardKey(pod.id) });
        void cache.invalidateQueries({ queryKey: previewKey(pod.id) });
        void cache.invalidateQueries({ queryKey: ["schedules", pod.id] });
    };

    const start = useMutation({ mutationFn: () => startScorecard(pod.id), onSuccess: refresh });
    const keep = useMutation({
        mutationFn: async () => {
            await ensureSuggestionsTable(pod.id);
            const body = createRequest({
                name: REVIEW_SCHEDULE,
                cron: REVIEW_CRON,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                target: "agent",
                agentName: "pod_default",
                workflowName: "",
                instruction: reviewInstruction(pod.name),
            });
            /* Shared, so everyone here sees the review in Standing work; it
               still runs as the person who turned it on. */
            await lemma(pod.id).request("POST", "/pods/" + pod.id + "/schedules", { body: { ...body, visibility: "POD" } });
        },
        onSuccess: refresh,
    });

    const history = new Map((preview.data?.measures ?? []).map((one) => [one.key, one] as const));
    const measures = card.data ?? [];
    const units = unitsOf(measures, history);
    const drafts = measures.filter((one) => !alwaysChecked(one) && !isProposal(one) && whyOff(one) === null);
    const proposals = measures.filter(isProposal);
    const uncountable = measures.filter((one) => !alwaysChecked(one) && !isProposal(one) && whyOff(one) !== null);
    const checked = measures.filter(alwaysChecked);

    const ask = (event: FormEvent) => {
        event.preventDefault();
        const said = words.trim();
        if (!said) return;
        onAskFor(wordsToMeasure(said));
        setWords("");
    };

    const goalsOn = drafts.filter((one) => one.on).length;
    /* Whoever the review reports to, said from where the reader stands. */
    const reader = review && !mine ? "the person who turned it on" : "you";

    return (
        <div className="aboutpage">
            <div className="aboutpage__column scoresetup">
                <ol className="scoresetup__steps" aria-label={"Setting up " + pod.name}>
                    <li data-state="done"><CheckIcon size={12} /> Hired</li>
                    <li data-state={review ? "done" : "now"} aria-current={review ? undefined : "step"}>{review ? <CheckIcon size={12} /> : null} Goals</li>
                    <li data-state={review ? "now" : "later"}>On the job</li>
                </ol>

                <header className="scoresetup__head">
                    <h1>How will you know {pod.name} is doing well?</h1>
                    <p>Set the goals {pod.name} is held to. Every Friday it counts its own week against them and tells you what to fix.</p>
                </header>

                {card.isPending && <p className="aboutpage__quiet">Loading…</p>}
                {card.isError && (
                    <p className="aboutpage__quiet" role="alert">
                        {isForbidden(card.error) ? "You may not read " + pod.name + "’s goals." : "Couldn’t load the goals."}{" "}
                        <button className="linkish" onClick={() => void card.refetch()}>Try again</button>
                    </p>
                )}

                {card.isSuccess && card.data === null && (
                    <div className="scoresetup__empty">
                        <p>{pod.name} has no goals yet.</p>
                        <button className="pill-button" onClick={() => start.mutate()} disabled={start.isPending}>{start.isPending ? "Setting up…" : "Set up goals"}</button>
                        {start.isError && <p className="aboutpage__error" role="alert">{isForbidden(start.error) ? "Only people who can change " + pod.name + " can set its goals." : "That didn’t work. Try again."}</p>}
                    </div>
                )}

                {card.isSuccess && card.data && (
                    <>
                        {units.length > 0 && (
                            <section className="scoresetup__part">
                                <p className="scoresetup__label">What the goals count</p>
                                <ul className="units">
                                    {units.map((unit) => (
                                        <li key={unit.table}>
                                            <span>
                                                <b>{unit.label}</b>
                                                <small>{(unit.count === null ? "Counting…" : unit.count + " in the last four weeks") + " · one row each in the " + unit.table + " table"}</small>
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            </section>
                        )}

                        {drafts.length > 0 && (
                            <section className="scoresetup__part">
                                <p className="scoresetup__label">Goals <span>· switch one off to stop counting it</span></p>
                                <ul className="measures">
                                    {drafts.map((measure) => (
                                        <MeasureRow key={measure.id} podId={pod.id} name={pod.name} measure={measure} history={history.get(measure.key)} counting={preview.isPending} onChanged={refresh} onAskFor={onAskFor} />
                                    ))}
                                </ul>
                            </section>
                        )}

                        {proposals.length > 0 && (
                            <section className="scoresetup__part">
                                <p className="scoresetup__label">Drafted from what you said</p>
                                <ul className="measures">
                                    {proposals.map((measure) => (
                                        <Proposal key={measure.id} podId={pod.id} measure={measure} history={history.get(measure.key)} onChanged={refresh} />
                                    ))}
                                </ul>
                            </section>
                        )}

                        <section className="scoresetup__part">
                            <p className="scoresetup__label">Add a goal in your own words</p>
                            <form className="scoresetup__words" onSubmit={ask}>
                                <label className="sr-only" htmlFor="judged-words">What would make you say {pod.name} is doing well?</label>
                                <input id="judged-words" value={words} onChange={(event) => setWords(event.target.value)} placeholder={"What would make you say " + pod.name + " is doing well?"} />
                                <button type="submit" aria-label="Ask for this goal" disabled={!words.trim()}><SendIcon size={15} /></button>
                            </form>
                            <p className="aboutpage__quiet scoresetup__hint">{pod.name} turns it into a goal it can count, checks it against your last four weeks, and shows it here for you to add.</p>
                        </section>

                        {uncountable.length > 0 && (
                            <section className="scoresetup__part">
                                <p className="scoresetup__label">Can’t be counted yet</p>
                                <ul className="scoresetup__cant">
                                    {uncountable.map((measure) => (
                                        <li key={measure.id}>
                                            <LockIcon size={14} />
                                            <span>{measure.measure}<small>{whyOff(measure)}</small></span>
                                        </li>
                                    ))}
                                </ul>
                            </section>
                        )}

                        {checked.length > 0 && (
                            <p className="scoresetup__always"><CheckIcon size={13} /> Also checked for every teammate: {sayList(checked.map((one) => one.measure.toLowerCase()))}.</p>
                        )}

                        <section className="scoresetup__review" aria-label="The Friday review">
                            <p className="scoresetup__label">The Friday review</p>
                            <p className="scoresetup__plain">
                                Every Friday at 4 pm, {pod.name} counts the week against these goals and messages {reader} the
                                results, with a fix suggested for each miss. Nothing changes until {reader === "you" ? "you approve" : "they approve"} a fix in About.
                            </p>
                            {review ? (
                                <p className="scoresetup__on" role="status">
                                    <CheckIcon size={14} /> The Friday review is on. The first one is {sayReviewDay(nextReview(new Date()))}.{" "}
                                    <button className="linkish" onClick={onAbout}>Open About <ArrowRightIcon size={13} /></button>
                                </p>
                            ) : (
                                <>
                                    <div className="scoresetup__acts">
                                        <button className="pill-button" onClick={() => keep.mutate()} disabled={keep.isPending || goalsOn === 0}>
                                            {keep.isPending ? "Turning it on…" : "Turn on the Friday review"}
                                        </button>
                                        <button className="ghost-pill" onClick={onAbout}>Not now</button>
                                    </div>
                                    {goalsOn === 0 && <p className="aboutpage__quiet scoresetup__hint">Add a goal first — there is nothing to count yet.</p>}
                                </>
                            )}
                            {keep.isError && (
                                <p className="aboutpage__error" role="alert">
                                    {isForbidden(keep.error) ? "Only people who can change " + pod.name + " can turn the review on." : keep.error instanceof Error ? keep.error.message : "That didn’t work. Try again."}
                                </p>
                            )}
                        </section>
                    </>
                )}
            </div>
        </div>
    );
}

/** "a", "a and b", "a, b and c". */
function sayList(items: string[]): string {
    return items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items.at(-1);
}

/** One goal: what it is, its weekly target and where it is counted from in
 *  words, and its last four weeks. The target is text, not a control: a
 *  person changes it by saying so, and the teammate redrafts and recounts. */
function MeasureRow({ podId, name, measure, history, counting, onChanged, onAskFor }: { podId: string; name: string; measure: Measure; history: MeasureHistory | undefined; counting: boolean; onChanged: () => void; onAskFor: (text: string) => void }) {
    const [error, setError] = useState<string | null>(null);
    const change = useMutation({
        mutationFn: (patch: { is_on: boolean }) => updateMeasure(podId, measure.id, patch),
        onSuccess: () => { setError(null); onChanged(); },
        onError: (problem) => setError(isForbidden(problem) ? "You may not change this." : "That change didn’t save."),
    });
    /* The newest week's reason, when it could not be counted at all. */
    const newest = history?.weeks.at(-1);
    const failed = newest?.status === "failed" ? newest.reason || "Counting it failed." : null;

    return (
        <li className="measure" data-off={!measure.on || undefined}>
            <button className="measure__switch" role="switch" aria-checked={measure.on} aria-label={"Count “" + measure.measure + "”"} disabled={change.isPending} onClick={() => change.mutate({ is_on: !measure.on })}><span /></button>
            <div className="measure__body">
                <span className="measure__what">{measure.measure}</span>
                <small>{goalLine(measure)}</small>
                {failed && (
                    <small className="measure__failed">
                        {name} couldn’t count this goal.{" "}
                        <button className="linkish" onClick={() => onAskFor(fixMeasure(measure.measure, failed))}>Ask {name} to fix it</button>
                    </small>
                )}
                {error && <small className="measure__error" role="alert">{error}</small>}
            </div>
            <span className="measure__history">
                {history ? (
                    <>
                        <Sparkline measure={measure} weeks={history.weeks} />
                        <small className="measure__when">Past four weeks</small>
                        <WeekNumber podId={podId} measure={measure} weeks={history.weeks} />
                    </>
                ) : counting ? <span className="aboutpage__quiet">Counting…</span> : null}
            </span>
        </li>
    );
}

/** "Target each week: 9 in 10 · counted from Callbacks". */
function goalLine(measure: Measure): string {
    return "Target each week: " + measure.targetLabel + (measure.countedFrom ? " · counted from " + measure.countedFrom : "");
}

/** A goal the teammate drafted from somebody's words: what they said, what it
 *  became, and its four weeks — added with one tap, or dropped. */
function Proposal({ podId, measure, history, onChanged }: { podId: string; measure: Measure; history: MeasureHistory | undefined; onChanged: () => void }) {
    const settle = useMutation({
        mutationFn: (keep: boolean) => (keep ? updateMeasure(podId, measure.id, { is_on: true, proposed_from: null }) : dropMeasure(podId, measure.id)),
        onSuccess: onChanged,
    });
    return (
        <li className="measure measure--proposal">
            <div className="measure__body">
                <small>You said “{measure.proposedFrom}”</small>
                <span className="measure__what">{measure.measure}</span>
                <small>{goalLine(measure)}</small>
                {settle.isError && <small className="measure__error" role="alert">That didn’t save. Try again.</small>}
            </div>
            <span className="measure__history">{history && <><Sparkline measure={measure} weeks={history.weeks} /><small className="measure__when">Past four weeks</small><WeekNumber podId={podId} measure={measure} weeks={history.weeks} /></>}</span>
            <span className="measure__acts">
                <button className="pill-button" disabled={settle.isPending} onClick={() => settle.mutate(true)}>Add goal</button>
                <button className="ghost-pill" disabled={settle.isPending} onClick={() => settle.mutate(false)}>Drop</button>
            </span>
        </li>
    );
}
