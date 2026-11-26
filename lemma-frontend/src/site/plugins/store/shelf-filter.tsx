"use client";

import { useRef, useState, type ReactNode } from "react";

import s from "./store.module.css";

/** The row of jobs over the shelf, and the shelf itself. Choosing a job hides
 *  the plugins that are not for it; every plugin is in the page from the
 *  start, so the shelf is whole without a script. */
export function ShelfFilter({ jobs, children }: { jobs: Array<{ id: string; name: string; count: number }>; children: ReactNode }) {
    const [job, setJob] = useState("");
    const shelf = useRef<HTMLUListElement>(null);
    const choose = (next: string) => {
        setJob(next);
        for (const card of shelf.current?.querySelectorAll<HTMLElement>("[data-jobs]") ?? []) {
            card.hidden = next !== "" && !card.dataset.jobs?.split(" ").includes(next);
        }
    };
    return (
        <>
            <div className={s.filter} role="group" aria-label="Show plugins for">
                <button type="button" aria-pressed={job === ""} onClick={() => choose("")}>
                    All
                </button>
                {jobs.map((each) => (
                    <button key={each.id} type="button" aria-pressed={job === each.id} onClick={() => choose(each.id)}>
                        {each.name}
                        <span className={s.filterCount}>{each.count}</span>
                    </button>
                ))}
            </div>
            <ul ref={shelf} className={s.shelf}>
                {children}
            </ul>
        </>
    );
}
