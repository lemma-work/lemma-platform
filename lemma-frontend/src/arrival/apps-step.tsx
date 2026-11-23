"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Job } from "lemma-sdk";
import { usePodMay } from "@/connect/pod-connect";
import { lemma } from "@/session/client";
import { captureEvent } from "@/site/analytics/client";
import { BUILT, listed, type BuiltPlugin } from "@/site/plugins/catalog";
import { Shot } from "@/site/plugins/shot";
import { AppGlyph } from "./glyphs";
import { flyInto } from "./fly";
import { LemmaMark, TickIcon } from "./shell";
import { addHref, picksThatCount, pluginsForJob, rankPlugins, suggestPlugins } from "./suggest";
import { JOBS, OTHER_LIMIT, workAnswer, type JobId, type WorkAnswer } from "./work";

/** How many of a job's apps show before "Show all": enough to choose
 *  between, few enough to read. */
const SHOWN = 4;
/** How many start picked: one press adds a working pair. */
const PICKED = 2;

export type { AddedApp } from "./install-run";

/** "What do you do? I'll add the apps that fit." -- the job question and its
 *  answer on one screen. Each job they pick gets a tab of every app for it,
 *  the one picked last in front; what they tick stays ticked across tabs,
 *  and the line under them names everything that goes in, and where. Any
 *  app opens in place to show its screens and what it does. Somebody who
 *  may not change this Lemma gets each app's own page instead. */
export function AppsStep({ name, podId, lemmaName, lemmaIcon, shared, asksWork, onDone, onStage, onLanded }: {
    name: string;
    podId: string | null;
    lemmaName: string;
    lemmaIcon: string | null;
    shared: boolean;
    /** Whether the job answer still needs saving: not for somebody who
     *  answered on another way in. */
    asksWork: boolean;
    onDone: (picked: BuiltPlugin[], answer: WorkAnswer) => void;
    /** What the Lemma on the left should hold: the picks, and which of them
     *  are still flying over. */
    onStage?: (picks: { slug: string; landing?: boolean }[]) => void;
    /** A pick just landed on the Lemma. */
    onLanded?: () => void;
}) {
    const queryClient = useQueryClient();
    /* In the order they were picked, so the latest is the tab in front. */
    const [jobs, setJobs] = useState<ReadonlySet<JobId>>(new Set());
    const [writing, setWriting] = useState(false);
    const [other, setOther] = useState("");
    const [picks, setPicks] = useState<ReadonlySet<string> | null>(null);
    const [tab, setTab] = useState<JobId | null>(null);
    const [all, setAll] = useState(false);
    const [open, setOpen] = useState<string | null>(null);

    const answer = workAnswer(jobs, writing ? other : "");
    const said = answer.jobs.length > 0 || Boolean(answer.other);
    const front = tab && jobs.has(tab) ? tab : [...jobs].at(-1) ?? null;
    const listedHere = !said ? [] : front ? pluginsForJob(front, answer.jobs, BUILT) : rankPlugins([], BUILT);
    /* Picked for them until they pick themselves: the two that fit most of
       what they said, following the chips as they change. */
    const picked = picks ?? new Set(said ? suggestPlugins(answer.jobs, BUILT, PICKED).map((one) => one.slug) : []);
    /* A job's first few, and whatever is picked further down: a tick never
       hides behind "Show more". */
    const shown = listedHere.filter((plugin, index) => all || index < SHOWN || picked.has(plugin.slug));
    const chosen = said ? picksThatCount(picked, answer.jobs, BUILT) : [];

    /* A tick flies its app over to the Lemma; until it lands its spot is
       kept open but not drawn. */
    const [landing, setLanding] = useState<ReadonlySet<string>>(new Set());
    const staged = chosen.map((one) => one.slug).join(" ") + "|" + [...landing].join(" ");
    useEffect(() => {
        onStage?.(chosen.map((one) => ({ slug: one.slug, landing: landing.has(one.slug) })));
    }, [staged]);

    const may = usePodMay(podId ?? "", "pod.update", Boolean(podId));
    const canAdd = Boolean(podId) && (may.allowed || !may.answered);

    const save = useMutation({
        mutationFn: async (skip: boolean) => {
            if (!asksWork) return { next: null, skip };
            const given = skip ? { jobs: [] as JobId[], other: null } : answer;
            const next = await lemma().users.setWork({ jobs: given.jobs.map((id) => Job[id]), ...(given.other ? { other: given.other } : {}) });
            return { next, skip };
        },
        onSuccess: ({ next, skip }) => {
            if (next) queryClient.setQueryData(["current-user"], next);
            captureEvent("onboarding.work_answered", { jobs: skip ? 0 : answer.jobs.length, other: !skip && Boolean(answer.other), skipped: skip || !said });
            onDone(skip || !canAdd ? [] : chosen, skip ? { jobs: [], other: null } : answer);
        },
    });

    const show = (job: JobId | null) => {
        setTab(job);
        setAll(false);
        setOpen(null);
    };
    const flipJob = (id: JobId) => {
        save.reset();
        const on = !jobs.has(id);
        setJobs((was) => {
            const next = new Set(was);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
        /* A job just picked comes to the front, to show what it adds. */
        if (on) show(id);
    };
    const flipApp = (slug: string) => {
        const on = !picked.has(slug);
        setPicks(() => {
            const next = new Set(picked);
            if (next.has(slug)) next.delete(slug);
            else next.add(slug);
            return next;
        });
        if (!on) return;
        setLanding((was) => new Set(was).add(slug));
        const from = document.querySelector(`.arx__opt[data-slug="${CSS.escape(slug)}"] .ar__glyph`);
        void flyInto(from, slug).then(() => {
            setLanding((was) => {
                const next = new Set(was);
                next.delete(slug);
                return next;
            });
            onLanded?.();
        });
    };

    const who = name.trim() ? `, ${name.trim()}` : "";
    const roles = JOBS.filter((job) => jobs.has(job.id));
    return (
        <form
            className="ar__form"
            onSubmit={(event) => {
                event.preventDefault();
                if (!save.isPending) save.mutate(false);
            }}
        >
            <h1 className="ar__t">What do you do{who}? I’ll add the apps that fit.</h1>
            <div className="ar__chips" role="group" aria-label="What you do">
                {JOBS.map((job) => (
                    <button key={job.id} type="button" className="ar__chip" aria-pressed={jobs.has(job.id)} onClick={() => flipJob(job.id)}>
                        {job.label}
                    </button>
                ))}
                <button type="button" className="ar__chip" aria-pressed={writing} onClick={() => { save.reset(); setWriting((was) => !was); }}>
                    Something else
                </button>
            </div>
            {writing && (
                <label className="ar__field">
                    <span>In your own words</span>
                    <input value={other} maxLength={OTHER_LIMIT} autoFocus placeholder="Run a restaurant, teach, write grants…" onChange={(event) => { save.reset(); setOther(event.target.value); }} />
                </label>
            )}
            {listedHere.length > 0 ? (
                <div className="arx">
                    {roles.length > 1 && (
                        <div className="arx__tabs" role="tablist" aria-label="Apps for each job">
                            {roles.map((job) => {
                                const ticked = chosen.filter((one) => one.jobs.includes(job.id)).length;
                                return (
                                    <button key={job.id} type="button" role="tab" className="arx__tab" aria-selected={job.id === front} onClick={() => show(job.id)}>
                                        {job.label}
                                        {ticked > 0 && <small aria-label={`${ticked} picked`}>{ticked}</small>}
                                    </button>
                                );
                            })}
                        </div>
                    )}
                    <ul className="arx__list" aria-label={front ? `Apps for ${JOBS.find((job) => job.id === front)?.label}` : "Apps"}>
                        {shown.map((plugin) => (
                            <AppRow
                                key={plugin.slug}
                                plugin={plugin}
                                on={picked.has(plugin.slug)}
                                canAdd={canAdd}
                                open={open === plugin.slug}
                                onOpen={() => setOpen((was) => (was === plugin.slug ? null : plugin.slug))}
                                onFlip={() => flipApp(plugin.slug)}
                            />
                        ))}
                    </ul>
                    {listedHere.length > shown.length && (
                        <button type="button" className="ar__quiet arx__all" onClick={() => setAll(true)}>
                            Show {listedHere.length - shown.length} more
                        </button>
                    )}
                    {canAdd && podId ? (
                        <p className="ar__dest">
                            <LemmaMark id={podId} name={lemmaName} iconUrl={lemmaIcon} size={22} />
                            <span>
                                {chosen.length > 0
                                    ? <>{listed(chosen.map((one) => one.name))} {chosen.length === 1 ? "goes" : "go"} into <b>{lemmaName}</b>{shared ? ", for everyone here." : ", your own Lemma."}</>
                                    : <>The apps you pick go into <b>{lemmaName}</b>{shared ? ", for everyone here." : ", your own Lemma."}</>}
                            </span>
                        </p>
                    ) : (
                        <p className="ar__note">Adding apps to {lemmaName} is for its editors. Each one’s page can add it to a Lemma of your own.</p>
                    )}
                </div>
            ) : (
                <p className="ar__note">Pick what you do and I’ll suggest the apps that fit.</p>
            )}
            {save.isError && (
                <p className="ar__problem" role="alert">That didn’t save. {save.error instanceof Error ? save.error.message : ""}</p>
            )}
            <div className="ar__acts">
                <span className="ar__sum">
                    <a className="ar__quiet" href="/templates" target="_blank" rel="noreferrer">See all {BUILT.length}</a>
                </span>
                <button type="button" className="ar__quiet" disabled={save.isPending} onClick={() => save.mutate(true)}>Skip</button>
                <button type="submit" className="ar__btn ar__btn--primary" disabled={save.isPending || (!said && asksWork)}>
                    {save.isPending ? "Saving…" : canAdd && chosen.length > 0 ? `Add ${chosen.length > 2 ? `${chosen.length} apps` : listed(chosen.map((one) => one.name))}` : "Continue"}
                </button>
            </div>
        </form>
    );
}

/** One app: what it does and reads, ticked to add it; pressed, it opens in
 *  place to show its screens, what it does, and where to try it. */
function AppRow({ plugin, on, canAdd, open, onOpen, onFlip }: {
    plugin: BuiltPlugin;
    on: boolean;
    canAdd: boolean;
    open: boolean;
    onOpen: () => void;
    onFlip: () => void;
}) {
    const href = addHref(plugin);
    const detail = `arx-${plugin.slug}`;
    return (
        <li className="arx__opt" data-slug={plugin.slug} data-on={on && canAdd ? "yes" : undefined} data-open={open ? "yes" : undefined}>
            <div className="arx__row">
                <AppGlyph slug={plugin.slug} size={44} />
                <button type="button" className="arx__about" aria-expanded={open} aria-controls={detail} onClick={onOpen}>
                    <span className="ar__name">{plugin.name}</span>
                    <span className="ar__line">{plugin.job}</span>
                    <span className="arx__needs">{needsLine(plugin)}</span>
                </button>
                <button type="button" className="arx__peek" aria-expanded={open} aria-controls={detail} onClick={onOpen}>
                    {open ? "Less" : "See it"}
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
                </button>
                {canAdd ? (
                    <button type="button" className="arx__tick" role="checkbox" aria-checked={on} aria-label={`Add ${plugin.name}`} onClick={onFlip}>
                        {on && <TickIcon size={12} />}
                    </button>
                ) : href ? (
                    <a className="ar__btn ar__btn--small" href={href} onClick={() => captureEvent("onboarding.plugin_added", { plugin: plugin.slug })}>Add</a>
                ) : null}
            </div>
            {open && <AppDetail plugin={plugin} id={detail} />}
        </li>
    );
}

/** An app up close: its own screens on made-up data, one at a time, what it
 *  does, and where to try it or read more without leaving the way in. */
function AppDetail({ plugin, id }: { plugin: BuiltPlugin; id: string }) {
    const [at, setAt] = useState(0);
    const pictures = plugin.pictures;
    const picture = pictures[at] ?? null;
    const step = (by: number) => setAt((was) => (was + by + pictures.length) % pictures.length);
    return (
        <div className="arx__detail" id={id}>
            {picture && (
                <figure className="arx__shot">
                    <Shot picture={picture} alt={picture.caption} sizes="(max-width: 900px) 100vw, 620px" />
                    <figcaption>
                        <span>{picture.caption}</span>
                        {pictures.length > 1 && (
                            <span className="arx__pager">
                                <button type="button" aria-label="Previous screen" onClick={() => step(-1)}>‹</button>
                                {at + 1} of {pictures.length}
                                <button type="button" aria-label="Next screen" onClick={() => step(1)}>›</button>
                            </span>
                        )}
                    </figcaption>
                </figure>
            )}
            <p className="arx__intro">{plugin.intro}</p>
            {plugin.does.length > 0 && (
                <ul className="arx__does">
                    {plugin.does.map((line) => <li key={line}>{line}</li>)}
                </ul>
            )}
            <p className="arx__links">
                {plugin.demo && <a className="ar__quiet" href={plugin.demo} target="_blank" rel="noreferrer">Try it on sample data</a>}
                <a className="ar__quiet" href={`/templates/${plugin.slug}`} target="_blank" rel="noreferrer">Its page</a>
            </p>
        </div>
    );
}

/** What an app reads, or what it builds on, in one line. */
export function needsLine(plugin: Pick<BuiltPlugin, "accounts" | "carries">): string {
    const account = plugin.accounts.find((one) => !one.optional);
    if (account) return `Reads ${listed(account.any, "or")}`;
    const carried = plugin.carries.map((slug) => BUILT.find((one) => one.slug === slug)?.name).filter((one): one is string => Boolean(one));
    return carried.length > 0 ? `Builds on ${listed(carried)}` : "Works on its own";
}
