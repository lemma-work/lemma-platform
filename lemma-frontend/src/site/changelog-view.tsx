"use client";

import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useRef,
    useState,
    type ReactNode,
} from "react";
import type { ChangelogKind } from "./changelog-notes";

/* What's new, in the browser: which kinds are shown, and which entries are
   open. The newest starts open; an entry reached by its address, or by the
   address of a heading inside it, opens too. The notes themselves are drawn
   on the server and only shown or hidden here. */

type Filter = "all" | ChangelogKind;

type ChangelogState = {
    filter: Filter;
    open: ReadonlySet<string>;
    toggle: (slug: string) => void;
};

const ChangelogContext = createContext<ChangelogState>({
    filter: "all",
    open: new Set(),
    toggle: () => {},
});

const FILTERS: { key: Filter; label: string }[] = [
    { key: "all", label: "All" },
    { key: "release", label: "Releases" },
    { key: "update", label: "Updates" },
];

export function ChangelogView({
    counts,
    kinds,
    newest,
    children,
}: {
    counts: Record<Filter, number>;
    kinds: Record<string, ChangelogKind>;
    newest: string | null;
    children: ReactNode;
}) {
    const [filter, setFilter] = useState<Filter>("all");
    const [open, setOpen] = useState<ReadonlySet<string>>(
        () => new Set(newest ? [newest] : []),
    );
    const scrollTo = useRef<string | null>(null);

    useEffect(() => {
        const reveal = () => {
            const id = decodeURIComponent(window.location.hash.slice(1));
            const target = id ? document.getElementById(id) : null;
            const entry = target?.closest<HTMLElement>("[data-entry]");
            const slug = entry?.dataset.entry;
            if (!target || !slug) return;
            setFilter((f) => (f === "all" || f === kinds[slug] ? f : "all"));
            setOpen((prev) => (prev.has(slug) ? prev : new Set(prev).add(slug)));
            /* A heading inside notes that were hidden has nowhere to scroll
               to until they show. */
            if (target !== entry) scrollTo.current = id;
        };
        reveal();
        window.addEventListener("hashchange", reveal);
        return () => window.removeEventListener("hashchange", reveal);
    }, [kinds]);

    useEffect(() => {
        if (!scrollTo.current) return;
        document.getElementById(scrollTo.current)?.scrollIntoView();
        scrollTo.current = null;
    }, [open]);

    const toggle = useCallback((slug: string) => {
        setOpen((prev) => {
            const next = new Set(prev);
            if (!next.delete(slug)) next.add(slug);
            return next;
        });
    }, []);
    const state = useMemo(() => ({ filter, open, toggle }), [filter, open, toggle]);

    return (
        <ChangelogContext.Provider value={state}>
            <div className="log-chips" role="group" aria-label="Show">
                {FILTERS.map(({ key, label }) => (
                    <button
                        key={key}
                        type="button"
                        className="log-chip"
                        aria-pressed={filter === key}
                        onClick={() => setFilter(key)}
                    >
                        {label}
                        <span>{counts[key]}</span>
                    </button>
                ))}
            </div>
            {children}
        </ChangelogContext.Provider>
    );
}

/** An entry, hidden while the chips leave its kind out. */
export function ChangelogEntryFrame({
    slug,
    kind,
    children,
}: {
    slug: string;
    kind: ChangelogKind;
    children: ReactNode;
}) {
    const { filter } = useContext(ChangelogContext);
    return (
        <article
            className="log-entry"
            id={slug}
            data-entry={slug}
            hidden={filter !== "all" && filter !== kind}
        >
            {children}
        </article>
    );
}

/** The entry's full notes, shown while it is open. */
export function ChangelogNotes({
    slug,
    children,
}: {
    slug: string;
    children: ReactNode;
}) {
    const { open } = useContext(ChangelogContext);
    return (
        <div className="log-notes" id={slug + "-notes"} hidden={!open.has(slug)}>
            {children}
        </div>
    );
}

/** "Read the notes", and "Show less" once they are open. Closing a long
 *  entry from its foot brings its top back into view rather than leaving
 *  the reader somewhere further down the page. */
export function ChangelogToggle({ slug }: { slug: string }) {
    const { open, toggle } = useContext(ChangelogContext);
    const isOpen = open.has(slug);
    const button = useRef<HTMLButtonElement>(null);
    const closing = useRef(false);
    useEffect(() => {
        if (isOpen || !closing.current) return;
        closing.current = false;
        const entry = button.current?.closest("article");
        if (entry && entry.getBoundingClientRect().top < 0)
            entry.scrollIntoView({ block: "start" });
    }, [isOpen]);
    return (
        <div className="log-more">
            <button
                ref={button}
                type="button"
                className={isOpen ? "b b--s b--quiet" : "b b--s b--secondary"}
                aria-expanded={isOpen}
                aria-controls={slug + "-notes"}
                onClick={() => {
                    closing.current = isOpen;
                    toggle(slug);
                }}
            >
                {isOpen ? "Show less" : "Read the notes"}
            </button>
        </div>
    );
}
