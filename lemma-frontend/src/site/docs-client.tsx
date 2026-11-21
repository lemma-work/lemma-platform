"use client";

import {
    useEffect,
    useId,
    useRef,
    useState,
    type KeyboardEvent,
    type ReactNode,
} from "react";
import { copyText } from "@/desktop/clipboard";
import { CheckIcon, CopyIcon } from "@/ui/icons";

/* The small live pieces of a docs page: copying, a block's language tabs,
   and "On this page" following the reader. */

type CopyState = "idle" | "copied" | "failed";

/** A button that copies `text`, and says so on itself: "Copied" for a
 *  moment, or "Couldn't copy" where the browser refused, and pressing it
 *  again tries again. */
export function CopyButton({
    text,
    label = "Copy",
    className = "b b--s b--quiet",
}: {
    text: string;
    label?: string;
    className?: string;
}) {
    const [state, setState] = useState<CopyState>("idle");
    const timer = useRef<number | undefined>(undefined);
    useEffect(() => () => window.clearTimeout(timer.current), []);
    const copy = async () => {
        window.clearTimeout(timer.current);
        let next: CopyState = "copied";
        try {
            await copyText(text);
        } catch {
            next = "failed";
        }
        setState(next);
        timer.current = window.setTimeout(
            () => setState("idle"),
            next === "copied" ? 1600 : 3200,
        );
    };
    return (
        <button
            type="button"
            className={className}
            onClick={copy}
            data-state={state}
        >
            {state === "copied" ? (
                <CheckIcon aria-hidden="true" />
            ) : (
                <CopyIcon aria-hidden="true" />
            )}
            <span aria-live="polite">
                {state === "copied"
                    ? "Copied"
                    : state === "failed"
                      ? "Couldn't copy"
                      : label}
            </span>
        </button>
    );
}

export type CodeTab = { label: string; code: string; panel: ReactNode };

/** One step written in more than one language: a tab each, and Copy takes
 *  the one showing. */
export function CodeTabs({ tabs }: { tabs: CodeTab[] }) {
    const [at, setAt] = useState(0);
    const id = useId();
    const buttons = useRef<(HTMLButtonElement | null)[]>([]);
    const move = (event: KeyboardEvent<HTMLButtonElement>) => {
        const step =
            event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        const next = (at + step + tabs.length) % tabs.length;
        setAt(next);
        buttons.current[next]?.focus();
    };
    return (
        <div className="docs-code">
            <div className="docs-code__bar">
                <div className="docs-code__tabs" role="tablist" aria-label="Language">
                    {tabs.map((tab, i) => (
                        <button
                            key={tab.label}
                            ref={(el) => {
                                buttons.current[i] = el;
                            }}
                            type="button"
                            role="tab"
                            id={`${id}-tab-${i}`}
                            aria-selected={i === at}
                            aria-controls={`${id}-panel-${i}`}
                            tabIndex={i === at ? 0 : -1}
                            onClick={() => setAt(i)}
                            onKeyDown={move}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
                <CopyButton text={tabs[at].code} />
            </div>
            {tabs.map((tab, i) => (
                <div
                    key={tab.label}
                    role="tabpanel"
                    id={`${id}-panel-${i}`}
                    aria-labelledby={`${id}-tab-${i}`}
                    hidden={i !== at}
                >
                    {tab.panel}
                </div>
            ))}
        </div>
    );
}

/** "On this page", with the section being read lit. A section is being read
 *  once its heading has passed the top third of the window; before the first
 *  one has, nothing is lit. */
export function DocsOutline({
    sections,
}: {
    sections: { id: string; title: string }[];
}) {
    const [active, setActive] = useState<string | null>(null);
    useEffect(() => {
        const headings = sections
            .map((s) => document.getElementById(s.id))
            .filter((el): el is HTMLElement => el !== null);
        if (!headings.length || typeof IntersectionObserver === "undefined")
            return;
        const read = () => {
            const mark = window.innerHeight * 0.3;
            let current: string | null = null;
            for (const heading of headings) {
                if (heading.getBoundingClientRect().top > mark) break;
                current = heading.id;
            }
            setActive(current);
        };
        /* The observed band is the window's top third, so a heading crossing
           its lower edge, either way, is what wakes this. */
        const observer = new IntersectionObserver(read, {
            rootMargin: "0px 0px -70% 0px",
        });
        headings.forEach((heading) => observer.observe(heading));
        return () => observer.disconnect();
    }, [sections]);
    return (
        <nav aria-label="On this page">
            <p className="docs-outline__title">On this page</p>
            {sections.map((section) => (
                <a
                    key={section.id}
                    href={"#" + section.id}
                    aria-current={section.id === active ? "location" : undefined}
                >
                    {section.title}
                </a>
            ))}
        </nav>
    );
}
