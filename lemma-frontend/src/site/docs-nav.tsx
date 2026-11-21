"use client";

import { useId } from "react";
import Link from "next/link";
import type { DocsNavGroup } from "./docs-content";

export type DocsDoor = "use" | "build";

/** The guide list: which half of the docs at its top, then that half's
 *  pages. Every page written so far is for builders; the other half lists
 *  what it will hold, none of it linked yet. */
export function DocsNav({
    nav,
    topics,
    door,
    onDoor,
    current,
    onNavigate,
}: {
    nav: DocsNavGroup[];
    topics: readonly string[];
    door: DocsDoor;
    onDoor: (door: DocsDoor) => void;
    /** The current page's slug, "" on the docs home. */
    current: string;
    onNavigate?: () => void;
}) {
    const id = useId();
    return (
        <div className="docs-list">
            <div className="docs-switch" role="group" aria-label="Which docs">
                <button
                    type="button"
                    aria-pressed={door === "use"}
                    onClick={() => onDoor("use")}
                >
                    Use Lemma
                </button>
                <button
                    type="button"
                    aria-pressed={door === "build"}
                    onClick={() => onDoor("build")}
                >
                    Build on Lemma
                </button>
            </div>
            {door === "use" ? (
                <section aria-labelledby={`${id}-use`}>
                    <p className="docs-list__h" id={`${id}-use`}>
                        Use Lemma
                    </p>
                    <ul>
                        {topics.map((topic) => (
                            <li key={topic} className="docs-list__soon">
                                {topic}
                                <span>Being written</span>
                            </li>
                        ))}
                    </ul>
                </section>
            ) : (
                <nav aria-label="Build on Lemma">
                    {nav.map((group, g) => (
                        <section key={group.title} aria-labelledby={`${id}-${g}`}>
                            <p className="docs-list__h" id={`${id}-${g}`}>
                                {group.title}
                            </p>
                            <ul>
                                {group.pages.map((page) => (
                                    <li key={page.slug}>
                                        <Link
                                            href={"/docs/" + page.slug}
                                            aria-current={
                                                page.slug === current
                                                    ? "page"
                                                    : undefined
                                            }
                                            onClick={onNavigate}
                                        >
                                            {page.title}
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    ))}
                </nav>
            )}
        </div>
    );
}
