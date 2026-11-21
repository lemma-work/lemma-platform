"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";
import { Hash } from "@phosphor-icons/react";
import { FileIcon, SearchIcon, TerminalIcon } from "@/ui/icons";
import { usePanelFocus } from "@/ui/focus-trap";
import {
    markMatches,
    searchDocs,
    suggestedDocs,
    type DocsHit,
} from "./docs-search";

const ICONS = { page: FileIcon, section: Hash, command: TerminalIcon };

/** Search the docs: pages, their sections and their commands, as you type.
 *  The arrows move, Enter opens, Escape closes, and focus goes back to
 *  whatever opened it. */
export function DocsSearchDialog({
    index,
    onClose,
}: {
    index: DocsHit[];
    onClose: () => void;
}) {
    const panel = useRef<HTMLDivElement>(null);
    const keys = usePanelFocus(panel, onClose);
    const router = useRouter();
    const [query, setQuery] = useState("");
    const [at, setAt] = useState(0);
    const id = useId();
    const typed = query.trim();
    const groups = useMemo(
        () => (typed ? searchDocs(index, typed) : suggestedDocs(index)),
        [index, typed],
    );
    const flat = groups.flatMap((group) => group.hits);
    const selected = Math.min(at, Math.max(0, flat.length - 1));
    const optionId = (i: number) => `${id}-hit-${i}`;

    useEffect(() => {
        document
            .getElementById(`${id}-hit-${selected}`)
            ?.scrollIntoView({ block: "nearest" });
    }, [id, selected]);

    const open = (hit: DocsHit) => {
        onClose();
        router.push(hit.href);
    };
    const onInputKey = (event: KeyboardEvent<HTMLInputElement>) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!flat.length) return;
            const step = event.key === "ArrowDown" ? 1 : -1;
            setAt((selected + step + flat.length) % flat.length);
        } else if (event.key === "Enter") {
            event.preventDefault();
            const hit = flat[selected];
            if (hit) open(hit);
        }
    };

    let n = -1;
    return (
        <div
            className="docs-find-scrim"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget) onClose();
            }}
        >
            <div
                ref={panel}
                className="docs-find"
                role="dialog"
                aria-modal="true"
                aria-label="Search the docs"
                onKeyDown={keys}
            >
                <label className="docs-find__in">
                    <SearchIcon aria-hidden="true" />
                    <input
                        type="text"
                        value={query}
                        onChange={(event) => {
                            setQuery(event.target.value);
                            setAt(0);
                        }}
                        onKeyDown={onInputKey}
                        placeholder="Search guides, commands and pages"
                        aria-label="Search the docs"
                        role="combobox"
                        aria-autocomplete="list"
                        aria-expanded={flat.length > 0}
                        aria-controls={`${id}-list`}
                        aria-activedescendant={
                            flat.length ? optionId(selected) : undefined
                        }
                        autoComplete="off"
                        autoCorrect="off"
                        spellCheck={false}
                    />
                    <kbd>Esc</kbd>
                </label>
                <div
                    className="docs-find__list"
                    id={`${id}-list`}
                    role="listbox"
                    aria-label="Results"
                >
                    {groups.map((group) => (
                        <div
                            key={group.label}
                            role="group"
                            aria-labelledby={`${id}-${group.kind}`}
                        >
                            <p
                                className="docs-find__group"
                                id={`${id}-${group.kind}`}
                            >
                                {group.label}
                            </p>
                            {group.hits.map((hit) => {
                                n += 1;
                                const i = n;
                                const Icon = ICONS[hit.kind];
                                return (
                                    <div
                                        key={hit.kind + hit.href + hit.title}
                                        id={optionId(i)}
                                        role="option"
                                        aria-selected={i === selected}
                                        className="docs-hit"
                                        data-kind={hit.kind}
                                        onMouseMove={() => {
                                            if (i !== selected) setAt(i);
                                        }}
                                        onMouseDown={(event) =>
                                            event.preventDefault()
                                        }
                                        onClick={() => open(hit)}
                                    >
                                        <Icon aria-hidden="true" />
                                        <span className="docs-hit__t">
                                            {markMatches(hit.title, typed).map(
                                                (piece, k) =>
                                                    piece.match ? (
                                                        <mark key={k}>
                                                            {piece.text}
                                                        </mark>
                                                    ) : (
                                                        piece.text
                                                    ),
                                            )}
                                        </span>
                                        <span className="docs-hit__w">
                                            {hit.where}
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
                    ))}
                </div>
                {typed && flat.length === 0 && (
                    <p className="docs-find__empty" role="status">
                        Nothing matches “{typed}”. Try a command or a
                        page&rsquo;s name.
                    </p>
                )}
                <div className="docs-find__foot" aria-hidden="true">
                    <span>
                        <kbd>↑</kbd> <kbd>↓</kbd> to move
                    </span>
                    <span>
                        <kbd>↵</kbd> to open
                    </span>
                    <span>
                        <kbd>Esc</kbd> to close
                    </span>
                </div>
            </div>
        </div>
    );
}
