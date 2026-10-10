"use client";

import { useId, type ReactNode } from "react";
import { ChatIcon, FileIcon, FolderIcon, LinkIcon, PlusIcon, UploadIcon } from "@/ui/icons";
import { EmptyPicture } from "./empty-art";
import type { Empty, EmptyAction } from "./empty-copy";

/** What an empty place can do, given what the page around it can reach.
 *  An action with no handler here is not drawn: a button that goes nowhere is
 *  worse than one fewer button. */
export type EmptyHandlers = {
    page?: () => void;
    upload?: () => void;
    folder?: () => void;
    chat?: () => void;
    row?: () => void;
    reach?: () => void;
    pages?: () => void;
    /** Put words in the chat box for the person to finish or send. */
    ask?: (text: string) => void;
};

function handlerFor(action: EmptyAction, on: EmptyHandlers): (() => void) | null {
    if (action.kind === "ask") {
        const ask = on.ask;
        return ask ? () => ask(action.text) : null;
    }
    return on[action.kind] ?? null;
}

function Glyph({ action }: { action: EmptyAction }) {
    switch (action.kind) {
        case "page":
        case "pages":
            return <FileIcon size={16} />;
        case "upload":
            return <UploadIcon size={16} />;
        case "folder":
            return <FolderIcon size={16} />;
        case "row":
            return <PlusIcon size={16} />;
        case "reach":
            return <LinkIcon size={16} />;
        default:
            return <ChatIcon size={16} />;
    }
}

/** An empty place in a space: a picture of what it will hold, one line on
 *  what belongs here and how it gets here, and a way to make the first one.
 *
 *  Drawn instead of the list, never under a list's headings: a header over
 *  nothing is a section somebody has to read to learn it is empty. */
export function SpaceEmpty({ empty, on, busy = null, compact = false, dropping = false, learn, children }: {
    empty: Empty;
    on: EmptyHandlers;
    /** The place's guide, for the moment it is most wanted: when there is
     *  nothing yet to learn from. */
    learn?: { label: string; onOpen: () => void };
    /** The action in flight, which is disabled and says so. */
    busy?: EmptyAction["kind"] | null;
    /** Beneath a table's own header row, where a picture of a table would
     *  be a second, emptier copy of the one right above it. */
    compact?: boolean;
    /** Files are being dragged over the place they would land in. */
    dropping?: boolean;
    children?: ReactNode;
}) {
    const primary = handlerFor(empty.primary, on);
    const starters = empty.starters.flatMap((action) => {
        const go = handlerFor(action, on);
        return go ? [{ action, go }] : [];
    });
    const working = busy !== null && busy === empty.primary.kind;
    const heading = useId();

    return (
        <section className={"empty" + (compact ? " empty--compact" : "") + (dropping ? " empty--drop" : "")} aria-labelledby={heading}>
            {!compact && <EmptyPicture art={empty.art} />}
            <h2 id={heading} className="empty__title">{empty.title}</h2>
            <p className="empty__line">{empty.line}</p>
            {(primary || starters.length > 0) && (
                <div className="empty__actions">
                    {primary && (
                        <button className="empty__primary" onClick={primary} disabled={working}>
                            <Glyph action={empty.primary} />
                            {working ? "Making…" : empty.primary.label}
                        </button>
                    )}
                    {starters.map(({ action, go }) => (
                        <button key={action.label} className="empty__starter" onClick={go}
                            title={action.kind === "ask" ? action.text.trim() : undefined}>
                            {action.label}
                        </button>
                    ))}
                </div>
            )}
            {learn && <button className="empty__learn" onClick={learn.onOpen}>{learn.label}</button>}
            {children}
        </section>
    );
}
