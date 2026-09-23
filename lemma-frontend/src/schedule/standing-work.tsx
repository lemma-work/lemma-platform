"use client";

import { useState } from "react";
import { isForbidden } from "@/session/auth-state";
import { ChevronDownIcon, ChevronRightIcon, PlusIcon, RefreshIcon } from "@/ui/icons";
import {
    useCreateSchedule, useRetryRun, useScheduleActive, useScheduleRuns, useScheduleTargets, useSchedules,
} from "./queries";
import {
    CADENCES, SCHEDULE_EDIT, agoOf, blankDraft, canRetry, draftProblems, healthOf, may,
    type ScheduleDraft, type StandingJob,
} from "./schedules";

/** What a teammate does without being asked, and whether it is still working.
 *
 *  Six words and a dot is not enough. A title, a cadence and a green light
 *  drop everything the API sends about how the thing is actually going, so a
 *  schedule that has errored five times running and been switched off by the
 *  platform's own breaker draws identically to one that fired this morning.
 *  The profile is the only place these appear, which makes "why did my
 *  competitor watch stop" a question nothing on screen can answer.
 *
 *  So: what fires it, what it runs — an agent or a workflow, named, which was
 *  nowhere on screen before — when it last fired, whether that worked, and the
 *  failure streak. Then the two controls that make the reading actionable:
 *  pause/resume, and a retry on a firing that failed.
 *
 *  Flat rows on a divider, like the agents list and the connector catalogue. A
 *  standing job is a line in a CV, not a card.
 */
export function StandingWork({ podId, teammate }: { podId: string; teammate: string }) {
    const schedules = useSchedules(podId);
    const [opened, setOpened] = useState<string | null>(null);
    const [writing, setWriting] = useState(false);

    const jobs = schedules.data ?? [];

    return (
        <div className="sched">
            {schedules.isPending && <p className="empty-row" role="status">Reading the schedules…</p>}

            {schedules.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(schedules.error)
                        ? "You may not read this teammate's schedules."
                        : "Couldn’t load schedules."}{" "}
                    <button className="linkish" onClick={() => void schedules.refetch()}>Try again</button>
                </p>
            )}

            {schedules.isSuccess && jobs.length === 0 && !writing && (
                <p className="empty-row">
                    No schedules yet. Set up recurring work when {teammate} is ready.
                </p>
            )}

            {jobs.length > 0 && (
                <div className="sched-list">
                    {/* Keyed by position as well as id: a payload can carry
                        more than one row with no id, and two of them would
                        otherwise share a key. */}
                    {jobs.map((job, at) => (
                        <Row
                            key={job.id || "unreadable-" + at}
                            podId={podId}
                            job={job}
                            open={opened === job.id && Boolean(job.id)}
                            onOpen={() => setOpened(opened === job.id ? null : job.id)}
                        />
                    ))}
                </div>
            )}

            {writing ? (
                <NewSchedule podId={podId} onDone={() => setWriting(false)} />
            ) : (
                schedules.isSuccess && (
                    <button className="sched-add" onClick={() => setWriting(true)}>
                        <PlusIcon size={14} aria-hidden="true" /> Create schedule
                    </button>
                )
            )}
        </div>
    );
}

/* ── one standing job ───────────────────────────────────────────────── */

function Row({ podId, job, open, onOpen }: {
    podId: string;
    job: StandingJob;
    open: boolean;
    onOpen: () => void;
}) {
    const health = healthOf(job);
    /* One mutation per row rather than one for the list, so a slow pause on
       one schedule does not put every other row's button into its pending
       state. */
    const active = useScheduleActive(podId);

    if (job.broken) {
        return (
            <div className="sched-row" data-tone="bad">
                <p className="sched-row__unreadable">
                    One row did not arrive as a schedule, so nothing can be said about it —
                    not what fires it, and not whether it is still running.
                </p>
            </div>
        );
    }

    return (
        <div className="sched-row" data-tone={health.tone} data-open={open || undefined}>
            <button className="sched-row__open" onClick={onOpen} aria-expanded={open}>
                <span className="sched-row__dot" aria-hidden="true" />
                <span className="sched-row__body">
                    <span className="sched-row__title">
                        <strong>{job.title}</strong>
                        {job.filter && <em className="sched-tag" title={job.filter}>filtered</em>}
                        {!job.active && <em className="sched-tag">paused</em>}
                    </span>

                    {/* What fires it, then what it runs. Two facts, one line
                        each, in the order somebody asks them. */}
                    <span className="sched-row__when">
                        {job.trigger}
                        {job.triggerLiteral && <code>{job.triggerLiteral}</code>}
                    </span>
                    {/* Which of the two it is, said in the sentence rather than
                        as a badge after it. A schedule runs an agent or a
                        workflow and those behave nothing alike, so naming
                        neither is the one thing not worth doing. */}
                    <span className="sched-row__target">
                        {job.target.kind === "none" && "Runs nothing"}
                        {/* A target whose name this caller may not read has a
                            label of its own — "an agent" — so it is not put
                            through the "the <kind> <name>" sentence. */}
                        {job.target.kind !== "none" && (job.target.name
                            ? "Runs the " + job.target.kind + " " + job.target.label
                            : "Runs " + job.target.label)}
                    </span>

                    {job.instruction && <small className="sched-row__detail">{job.instruction}</small>}

                    {health.line && <span className="sched-row__health">{health.line}</span>}
                    {job.lastError && health.tone === "bad" && (
                        <span className="sched-row__error" title={job.lastError}>{job.lastError}</span>
                    )}
                </span>

                <span className="sched-row__last">
                    {job.lastFiredAt ? "fired " + agoOf(job.lastFiredAt) : "not triggered yet"}
                </span>
                {open ? <ChevronDownIcon size={15} /> : <ChevronRightIcon size={15} />}
            </button>

            <div className="sched-row__acts">
                <button
                    className="btn"
                    disabled={!may(job, SCHEDULE_EDIT) || active.isPending}
                    title={may(job, SCHEDULE_EDIT) ? undefined : "You may read this schedule, not change it."}
                    onClick={() => active.mutate({ id: job.id, active: !job.active })}
                >
                    {active.isPending ? "…" : job.active ? "Pause" : "Resume"}
                </button>
            </div>

            {active.isError && (
                <p className="sched-row__problem" role="alert">
                    {active.error instanceof Error ? active.error.message : "Couldn’t update this schedule."}
                </p>
            )}

            {open && <Runs podId={podId} job={job} />}
        </div>
    );
}

/* ── its firings ────────────────────────────────────────────────────── */

function Runs({ podId, job }: { podId: string; job: StandingJob }) {
    const runs = useScheduleRuns(podId, job.id);
    const retry = useRetryRun(podId, job.id);
    const mayRetry = may(job, SCHEDULE_EDIT);

    return (
        <div className="sched-runs">
            {runs.isPending && <p className="sched-runs__note" role="status">Loading trigger history…</p>}
            {runs.isError && (
                <p className="sched-runs__note" role="alert">
                    {isForbidden(runs.error)
                        ? "You don’t have permission to view this schedule’s trigger history."
                        : "Couldn’t load trigger history."}{" "}
                    <button className="linkish" onClick={() => void runs.refetch()}>Try again</button>
                </p>
            )}
            {runs.isSuccess && runs.data.length === 0 && (
                <p className="sched-runs__note">No triggers recorded yet.</p>
            )}

            {(runs.data ?? []).map((run, at) => (
                <div className="sched-run" key={run.id || "unreadable-" + at} data-tone={run.tone}>
                    <span className="sched-run__dot" aria-hidden="true" />
                    <span className="sched-run__what">
                        {run.outcome}
                        {run.retryOf && <em className="sched-tag">a retry</em>}
                        {run.attempts > 1 && <em className="sched-tag">{run.attempts} attempts</em>}
                    </span>
                    <span className="sched-run__error">{run.error}</span>
                    <span className="sched-run__at">{agoOf(run.at)}</span>
                    {canRetry(run) && (
                        <button
                            className="btn btn--small"
                            disabled={!mayRetry || (retry.isPending && retry.variables === run.id)}
                            title={mayRetry ? "Run it again with the same event" : "You may read this schedule, not change it."}
                            onClick={() => retry.mutate(run.id)}
                        >
                            <RefreshIcon size={13} aria-hidden="true" />
                            {retry.isPending && retry.variables === run.id ? "Sending…" : "Retry"}
                        </button>
                    )}
                </div>
            ))}

            {retry.isError && (
                <p className="sched-runs__note" role="alert">
                    {retry.error instanceof Error ? retry.error.message : "Couldn’t retry this trigger."}
                </p>
            )}
            {/* Said once, under the ledger, rather than as a tooltip nobody
                opens: the retry makes a new row rather than changing the one
                that failed, which is why the failed row stays failed. */}
            {retry.isSuccess && (
                <p className="sched-runs__note">Retry queued. A new entry will appear in the trigger history.</p>
            )}
        </div>
    );
}

/* ── putting something new on a clock ───────────────────────────────── */

/** Only a clock, and the form says so.
 *
 *  A WEBHOOK schedule needs a connected account and a connector trigger id,
 *  and a DATASTORE one needs a table plus the operations the target is built
 *  to handle. Neither is a form this section could put in front of somebody
 *  without asking them to know things the API knows — so neither is offered
 *  here, and the ones that already exist are still read and paused like any
 *  other.
 */
function NewSchedule({ podId, onDone }: { podId: string; onDone: () => void }) {
    const [draft, setDraft] = useState<ScheduleDraft>(blankDraft);
    const [tried, setTried] = useState(false);
    const targets = useScheduleTargets(podId, true);
    const create = useCreateSchedule(podId);

    const wrong = draftProblems(draft);
    const change = (patch: Partial<ScheduleDraft>) => setDraft((was) => ({ ...was, ...patch }));
    const picked = draft.target === "workflow" ? "workflow:" + draft.workflowName : "agent:" + draft.agentName;

    function submit() {
        setTried(true);
        if (Object.keys(wrong).length > 0) return;
        create.mutate(draft, { onSuccess: onDone });
    }

    return (
        <div className="sched-new">
            <label className="sched-field">
                <span>Name</span>
                <input
                    value={draft.name}
                    placeholder="daily tracker refresh"
                    onChange={(event) => change({ name: event.target.value })}
                />
            </label>
            {tried && wrong.name && <p className="sched-field__problem">{wrong.name}</p>}

            <label className="sched-field">
                <span>Frequency</span>
                <select
                    value={CADENCES.some((one) => one.cron === draft.cron) ? draft.cron : "custom"}
                    onChange={(event) => event.target.value !== "custom" && change({ cron: event.target.value })}
                >
                    {CADENCES.map((one) => <option key={one.cron} value={one.cron}>{one.label}</option>)}
                    <option value="custom">Something else</option>
                </select>
            </label>

            {/* The cron is a field, not a caption. A picker that says "every
                weekday" and writes something else is worse than no picker, so
                what will actually be stored is editable and on screen at all
                times — the presets above are a shortcut into this box. */}
            <label className="sched-field">
                <span>Cron</span>
                <input
                    className="sched-field__mono"
                    value={draft.cron}
                    spellCheck={false}
                    onChange={(event) => change({ cron: event.target.value })}
                />
            </label>
            {tried && wrong.cron && <p className="sched-field__problem">{wrong.cron}</p>}

            <label className="sched-field">
                <span>Time zone</span>
                <input
                    className="sched-field__mono"
                    value={draft.timezone}
                    placeholder="UTC"
                    spellCheck={false}
                    onChange={(event) => change({ timezone: event.target.value })}
                />
            </label>

            <label className="sched-field">
                <span>Run</span>
                <select
                    value={picked}
                    onChange={(event) => {
                        const [kind, ...rest] = event.target.value.split(":");
                        const name = rest.join(":");
                        change(kind === "workflow"
                            ? { target: "workflow", workflowName: name }
                            : { target: "agent", agentName: name });
                    }}
                >
                    <option value="agent:">Pick one…</option>
                    {(targets.data ?? []).filter((one) => one.kind === "agent").map((one) => (
                        <option key={"agent:" + one.name} value={"agent:" + one.name}>{one.label}</option>
                    ))}
                    {(targets.data ?? []).filter((one) => one.kind === "workflow").map((one) => (
                        <option key={"workflow:" + one.name} value={"workflow:" + one.name}>
                            {one.label} (workflow)
                        </option>
                    ))}
                </select>
            </label>
            {tried && wrong.target && <p className="sched-field__problem">{wrong.target}</p>}

            <label className="sched-field sched-field--tall">
                <span>Instructions</span>
                <textarea
                    rows={3}
                    value={draft.instruction}
                    placeholder="What your teammate should do each time the schedule runs."
                    onChange={(event) => change({ instruction: event.target.value })}
                />
            </label>
            {/* Not a client rule, because it depends on the resolved agent —
                an agent with a standing instruction of its own needs none here
                and one without is refused. Said rather than guessed at. */}
            <p className="sched-new__note">
                Add instructions for an agent that does not have its own. Workflows use their configured steps.
            </p>

            {create.isError && (
                <p className="sched-field__problem" role="alert">
                    {create.error instanceof Error ? create.error.message : "Couldn’t create the schedule."}
                </p>
            )}

            <div className="sched-new__acts">
                <button className="btn btn--primary" disabled={create.isPending} onClick={submit}>
                    {create.isPending ? "Setting it up…" : "Create schedule"}
                </button>
                <button className="btn" disabled={create.isPending} onClick={onDone}>Cancel</button>
            </div>
        </div>
    );
}
