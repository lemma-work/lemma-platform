"use client";

import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import s from "./page.module.css";

/** The app's real pictures, one at a time, picked by their captions. The
 *  pictures themselves are drawn on the server (`Shot`, light and dark) and
 *  handed in, so this only chooses which one shows. */
export function Gallery({ name, captions, pictures }: { name: string; captions: string[]; pictures: ReactNode[] }) {
    const [at, setAt] = useState(0);
    const tabs = useRef<Array<HTMLButtonElement | null>>([]);
    const id = useId();
    const many = pictures.length > 1;

    function onKey(event: KeyboardEvent<HTMLDivElement>) {
        const last = pictures.length - 1;
        const next =
            event.key === "ArrowRight" || event.key === "ArrowDown"
                ? (at + 1) % pictures.length
                : event.key === "ArrowLeft" || event.key === "ArrowUp"
                  ? (at + last) % pictures.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? last
                      : -1;
        if (next < 0) return;
        event.preventDefault();
        setAt(next);
        tabs.current[next]?.focus();
    }

    return (
        <div className={s.gallery}>
            <div className={s.frame}>
                {pictures.map((picture, i) => (
                    <div
                        key={captions[i]}
                        id={`${id}-panel-${i}`}
                        role={many ? "tabpanel" : undefined}
                        aria-labelledby={many ? `${id}-tab-${i}` : undefined}
                        hidden={i !== at}
                    >
                        {picture}
                    </div>
                ))}
            </div>
            {many ? (
                <div className={s.tabs} role="tablist" aria-label={`Pictures of ${name}`} onKeyDown={onKey}>
                    {captions.map((caption, i) => (
                        <button
                            key={caption}
                            ref={(node) => {
                                tabs.current[i] = node;
                            }}
                            type="button"
                            role="tab"
                            id={`${id}-tab-${i}`}
                            aria-selected={i === at}
                            aria-controls={`${id}-panel-${i}`}
                            tabIndex={i === at ? 0 : -1}
                            onClick={() => setAt(i)}
                        >
                            <span className={s.tabNumber}>{i + 1}</span>
                            {caption}
                        </button>
                    ))}
                </div>
            ) : (
                <p className={s.caption}>{captions[0]}</p>
            )}
        </div>
    );
}
