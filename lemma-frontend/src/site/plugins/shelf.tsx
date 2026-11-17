"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PLUGINS, STAGE_NAME, byStage, partsOf, stageOf } from "./catalog";
import { AppCover } from "./cover";
import { JOBS, SHARED_TABLES, jobName } from "./tables";
import type { Job, Plugin, Stage } from "./types";
import s from "./plugins.module.css";

/** Jobs that have at least one plugin, in the filter's order. */
const SHOWN = JOBS.filter((j) => PLUGINS.some((p) => p.jobs.includes(j.id)));

/** With no filter, each plugin shows once, under the job it belongs to; the
 *  plugins for any job come first, since the others stand on them. */
const GROUPS: Job[] = [
    "everyone",
    ...JOBS.map((j) => j.id).filter((j) => j !== "everyone"),
];

/** The stages as a count reads them: "10 run today". */
const TALLY_NAME: Record<Stage, string> = {
    ready: "ready to install",
    runs: "run today",
    partly: "partly built",
    planned: "not built yet",
};

const TALLY = (["ready", "runs", "partly", "planned"] as Stage[]).map(
    (stage) => ({
        stage,
        n: PLUGINS.filter((p) => stageOf(p) === stage).length,
    }),
);

function readJob(): Job | null {
    const wanted = new URLSearchParams(window.location.search).get("job");
    return SHOWN.find((j) => j.id === wanted)?.id ?? null;
}

/**
 * The plugin shelf, filtered by job. The job sits in the address
 * (`?job=sales`) so a filtered shelf can be shared; it is read after the
 * first paint, so the page itself stays static. Plugins with apps show as
 * covers; the rest, not built yet, as one line each.
 */
export function PluginShelf() {
    const [job, setJob] = useState<Job | null>(null);

    useEffect(() => {
        setJob(readJob());
        const onPop = () => setJob(readJob());
        window.addEventListener("popstate", onPop);
        return () => window.removeEventListener("popstate", onPop);
    }, []);

    const pick = (next: Job | null) => {
        setJob(next);
        const url = new URL(window.location.href);
        if (next) url.searchParams.set("job", next);
        else url.searchParams.delete("job");
        window.history.replaceState(null, "", url);
    };

    const groups = job
        ? [{ job, plugins: PLUGINS.filter((p) => p.jobs.includes(job)) }]
        : GROUPS.map((g) => ({
              job: g,
              plugins: PLUGINS.filter((p) => p.jobs[0] === g),
          }));

    return (
        <div className={s.shelf}>
            <p className={s.tally}>
                {TALLY.map((t) => (
                    <span key={t.stage} data-stage={t.stage}>
                        <b>{t.n}</b> {TALLY_NAME[t.stage]}
                    </span>
                ))}
            </p>
            <div className={s.filters} role="group" aria-label="Filter by job">
                <button
                    type="button"
                    aria-pressed={job === null}
                    onClick={() => pick(null)}
                >
                    All <span>{PLUGINS.length}</span>
                </button>
                {SHOWN.map((j) => (
                    <button
                        key={j.id}
                        type="button"
                        aria-pressed={job === j.id}
                        onClick={() => pick(j.id)}
                    >
                        {j.name}{" "}
                        <span>
                            {PLUGINS.filter((p) => p.jobs.includes(j.id)).length}
                        </span>
                    </button>
                ))}
            </div>
            <div aria-live="polite">
                {groups.map((g) => (
                    <Group key={g.job} job={g.job} plugins={g.plugins} />
                ))}
            </div>
        </div>
    );
}

function Group({ job, plugins }: { job: Job; plugins: Plugin[] }) {
    const sorted = [...plugins].sort(byStage);
    const covered = sorted.filter((p) => partsOf(p)?.apps.length);
    const lines = sorted.filter((p) => !partsOf(p)?.apps.length);
    return (
        <section className={s.group}>
            <h2 className={s.groupHead}>
                {jobName(job)} <span>{plugins.length}</span>
            </h2>
            {covered.length > 0 && (
                <ul className={s.cards}>
                    {covered.map((p) => (
                        <li key={p.slug}>
                            <Card plugin={p} />
                        </li>
                    ))}
                </ul>
            )}
            {lines.length > 0 && (
                <ul className={s.rows}>
                    {lines.map((p) => (
                        <li key={p.slug}>
                            <Link
                                className={s.rowLink}
                                href={`/templates/${p.slug}`}
                            >
                                <span className={s.rowName}>{p.name}</span>
                                <span className={s.rowLine}>{p.headline}</span>
                                <span
                                    className={s.stage}
                                    data-stage={stageOf(p)}
                                >
                                    {STAGE_NAME[stageOf(p)]}
                                </span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}

function Card({ plugin: p }: { plugin: Plugin }) {
    const apps = partsOf(p)?.apps ?? [];
    const stage = stageOf(p);
    return (
        <Link className={s.cardLink} href={`/templates/${p.slug}`}>
            {apps[0] && <AppCover app={apps[0]} small />}
            <span className={s.cardName}>{p.name}</span>
            <span className={s.cardLine}>{p.headline}</span>
            <span className={s.cardMeta}>
                {apps.map((a) => a.title).join(" · ")}
            </span>
            <span className={s.cardMeta}>
                {p.makes.length
                    ? "Makes " +
                      p.makes.map((k) => SHARED_TABLES[k].name).join(", ")
                    : "Makes no shared table"}
            </span>
            <span className={s.stage} data-stage={stage}>
                {STAGE_NAME[stage]}
            </span>
        </Link>
    );
}
