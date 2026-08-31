'use client';

import { useEffect, useRef, useState } from 'react';

/** How long a whole rename takes, however long the title turns out to be. */
const RENAME_DURATION_MS = 700;
const MIN_STEP_MS = 12;
const MAX_STEP_MS = 32;

/**
 * One tick per character, paced so a 120-character title does not spend four
 * seconds typing itself into a row nobody is looking at any more.
 */
export function renameStepMs(characterCount: number): number {
    if (characterCount <= 0) return MAX_STEP_MS;
    return Math.min(MAX_STEP_MS, Math.max(MIN_STEP_MS, Math.round(RENAME_DURATION_MS / characterCount)));
}

export type RenamePlan =
    /** Put the new title up whole, with no animation. */
    | { kind: 'settle'; text: string }
    /** Type it in, one code point per `stepMs`. */
    | { kind: 'type'; characters: string[]; stepMs: number };

/**
 * Whether a title change is a rename worth showing, and how fast to show it.
 *
 * Separated from the hook because this is the whole decision: everything around
 * it is a timer.
 */
export function planRename(previous: string, next: string, reducedMotion: boolean): RenamePlan {
    // A row rendering its title for the first time is being named, not renamed —
    // which is what every row in a freshly loaded list is doing, and fifteen of
    // them typing at once is a screen that cannot be read. Losing a title is not
    // a rename either: there is nothing to type.
    if (!previous || !next || reducedMotion) return { kind: 'settle', text: next };

    // Code points, not code units: a title is generated in the writer's own
    // script, and slicing through a surrogate pair renders a replacement box.
    const characters = Array.from(next);
    return { kind: 'type', characters, stepMs: renameStepMs(characters.length) };
}

function prefersReducedMotion(): boolean {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Types a title in when it changes under a row that is already on screen.
 *
 * A conversation is named twice: once locally, from the first thing you typed,
 * and once by the server a few seconds later, from a model that read the same
 * message. The second one arrives on the conversation's own stream while the
 * agent is still answering, so the row rewrites itself under your eyes. With
 * nothing marking the moment that reads as a glitch — the title you were looking
 * at is simply a different title now. Typing it in says the rename happened, and
 * that something did it on purpose.
 */
export function useTypedRename(title: string): string {
    const [typed, setTyped] = useState(title);
    const previousRef = useRef(title);

    useEffect(() => {
        const previous = previousRef.current;
        previousRef.current = title;
        if (title === previous) return;

        const plan = planRename(previous, title, prefersReducedMotion());
        if (plan.kind === 'settle') {
            setTyped(plan.text);
            return;
        }

        let count = 1;
        setTyped(plan.characters[0] ?? '');
        const timer = setInterval(() => {
            count += 1;
            setTyped(plan.characters.slice(0, count).join(''));
            if (count >= plan.characters.length) clearInterval(timer);
        }, plan.stepMs);

        return () => clearInterval(timer);
    }, [title]);

    return typed;
}
