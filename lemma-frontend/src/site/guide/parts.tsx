import type { ReactNode } from "react";

import { chapter, section } from "./contents";

/** A chapter's opening band. */
export function Chapter({ n }: { n: number }) {
    const it = chapter(n);
    return (
        <section className="gb-band" id={it.id} data-chapter={it.n} aria-labelledby={`${it.id}-h`}>
            <div className="gb-g">
                <p className="gb-k gb-lab">chapter {it.word}</p>
                <h2 id={`${it.id}-h`}>{it.title}</h2>
            </div>
        </section>
    );
}

/** One numbered section, its number in the label column and its title above the text. */
export function Section({ id, children }: { id: string; children: ReactNode }) {
    const it = section(id);
    return (
        <section className="gb-s" id={id} aria-labelledby={`${id}-h`}>
            <div className="gb-g">
                <p className="gb-k gb-lab">{it.n}</p>
                <div className="gb-c">
                    <h3 id={`${id}-h`}>{it.title}</h3>
                    {children}
                </div>
            </div>
        </section>
    );
}

/** A full-width coral band for a line that matters more than the text around it. */
export function Statement({ label, line, children }: { label: string; line: string; children?: ReactNode }) {
    return (
        <section className="gb-state">
            <div className="gb-g">
                <p className="gb-k gb-lab">{label}</p>
                <div className="gb-c">
                    <p className="gb-big gb-wide">{line}</p>
                    {children}
                </div>
            </div>
        </section>
    );
}

/** A numbered figure. Its title says what it shows; `note` says what to notice. */
export function Figure({ n, title, note, className, children }: { n: number; title: string; note?: string; className?: string; children: ReactNode }) {
    return (
        <figure className={`gb-fig gb-wide${className ? ` ${className}` : ""}`}>
            <figcaption className="gb-fk">
                <span className="gb-k">fig. {n}</span>
                <span className="gb-ft">{title}</span>
            </figcaption>
            {children}
            {note && <p className="gb-fnote">{note}</p>}
        </figure>
    );
}
