"use client";

import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { listStamp } from "@/data/stamp";
import { isForbidden, isMissing } from "@/session/auth-state";
import { changedLately, MEMORY_FOLDERS, notesFrom } from "@/thread/memory-notes";
import { LockIcon } from "@/ui/icons";

/** What the teammate has written down while it worked, beside what people
 *  taught it. Taught is skills, which people write; this is memory, which it
 *  writes. The pair is how "it learns how you work" becomes something you can
 *  open instead of something you are told.
 *
 *  Read from the files themselves, so it is the notes that exist and nothing
 *  else: no summary, no count of lessons, nothing the model was asked to
 *  report about itself. A dot marks a note changed in the last week. Private
 *  notes are the viewer's own, and say so. */
export function WhatItRemembers({ pod, onFile }: { pod: Pod; onFile: (path: string) => void }) {
    const notes = useQuery({
        queryKey: ["memory-notes", pod.id],
        queryFn: async () => notesFrom(await Promise.all(MEMORY_FOLDERS.map(async (folder) => {
            try {
                return { private: folder.private, items: (await source.listLibrary(pod.id, "files", folder.path)).items };
            } catch (problem) {
                /* A folder that was never written to is not a failure: it is
                   a teammate that has not noted anything there yet. */
                if (isMissing(problem)) return { private: folder.private, items: [] };
                throw problem;
            }
        }))),
        staleTime: 60_000,
    });

    if (notes.isPending) return <p className="aboutpage__quiet">Loading…</p>;
    if (notes.isError) {
        return (
            <p className="aboutpage__quiet" role="alert">
                {isForbidden(notes.error) ? "You may not read " + pod.name + "’s notes." : "Couldn’t load " + pod.name + "’s notes."}{" "}
                <button className="linkish" onClick={() => void notes.refetch()}>Try again</button>
            </p>
        );
    }
    if (notes.data.length === 0) {
        return <p className="aboutpage__quiet">{pod.name} hasn’t written anything down yet. Correct it once and it will.</p>;
    }
    const now = Date.now();
    return (
        <ul className="notes">
            {notes.data.map((note) => {
                const fresh = changedLately(note.updated, now);
                return (
                    <li key={note.path}>
                        <button className="notes__row" onClick={() => onFile(note.path)} title={note.path}>
                            <span className={fresh ? "notes__dot notes__dot--fresh" : "notes__dot"} aria-label={fresh ? "Changed this week" : undefined} />
                            <span className="notes__line">
                                {note.private && <LockIcon size={11} aria-label="Only you" />}
                                <span className="notes__topic">{note.topic}</span>
                                {note.gloss && <span className="notes__gloss">{note.gloss}</span>}
                            </span>
                            <time className="notes__at" dateTime={note.updated}>{listStamp(note.updated)}</time>
                        </button>
                    </li>
                );
            })}
        </ul>
    );
}
