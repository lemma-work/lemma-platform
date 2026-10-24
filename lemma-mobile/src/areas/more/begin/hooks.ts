import { useCallback, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { identityIcon, variantFor } from "../../admin/about/faces";
import { problemOf } from "../../admin/words";
import { moreKey, useMoreSource } from "../hooks";
import { characterOf, freeName, isNameConflict, sameStart, setupAsk, startPlan, takenSentence, type Start, type StepId } from "./model";
import type { MadeLemma } from "./source";
import { characterForSeed } from "@web/shell/cast";

/** This part's reads and writes, through react-query. Every key is
 *  `moreKey(useCacheKey(), "begin", …)`; the Lemmas list is the app's own
 *  (`[cacheKey, "lemmas", orgId]`, `useLemmas` in `data/hooks.ts`). */
export function useBeginSource() {
    return useMoreSource().begin;
}

/** How a start finished. */
export interface Started {
    made: MadeLemma;
    /** The face stored, or 0 when none was (the reveal draws what it kept). */
    variant: number;
    /** False when the face could not be stored, so it kept its own. */
    faceSaved: boolean;
    /** Null for a blank start, which writes none; false when the write
     *  failed, which the Lemma's own setup then writes instead. */
    planSaved: boolean | null;
    /** The chat where it sets itself up; null for a blank start. */
    conversationId: string | null;
    asked: string | null;
    needsModel: boolean;
}

interface Progress {
    start: Start;
    made?: MadeLemma;
    variant?: number;
    faceSaved?: boolean;
    planSaved?: boolean;
    conversationId?: string;
}

function doneOf(held: Progress): StepId[] {
    const done: StepId[] = [];
    if (held.made) done.push("made");
    if (held.faceSaved !== undefined) done.push("face");
    if (held.planSaved !== undefined) done.push("plan");
    if (held.conversationId) done.push("ask");
    return done;
}

/** Starting a Lemma, step by step, each ticked as it really finishes.
 *
 *  What a first attempt made is kept, so trying the same start again after
 *  a failure finishes that Lemma rather than making a second one under the
 *  same name (the web's `makeTeammate`); choosing a different start lets it
 *  go. A face that will not save never fails the start: a Lemma with the
 *  wrong face is a Lemma, and one that exists twice is a mess. A plan that
 *  will not write does not either, as on the `/o` frame: the Lemma is asked
 *  to write its own. */
export function useStart(orgId: string | null) {
    const source = useBeginSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [start, setStart] = useState<Start | null>(null);
    const [done, setDone] = useState<StepId[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [running, setRunning] = useState(false);
    const progress = useRef<Progress | null>(null);

    const tick = (id: StepId) => setDone((was) => (was.includes(id) ? was : [...was, id]));

    const run = useCallback(async (next: Start, taken: readonly string[]): Promise<Started | null> => {
        if (!orgId) return null;
        if (!progress.current || !sameStart(progress.current.start, next)) progress.current = { start: next };
        const held = progress.current;
        setStart(next);
        setError(null);
        setRunning(true);
        setDone(doneOf(held));
        /* Asked alongside the steps, read at the end. */
        const model = source.needsModel(orgId).catch(() => false);
        try {
            if (!held.made) {
                const about = next.kind === "own" ? next.outcome : next.job;
                /* An owned start takes the next free name, here and on the
                   server, which may know a Lemma this person cannot see. A
                   name somebody typed is theirs, and a clash is said. */
                const seen = new Set(taken.map((one) => one.toLowerCase()));
                let name = next.name;
                for (let attempt = 0; ; attempt += 1) {
                    try {
                        held.made = await source.create(orgId, name, about);
                        break;
                    } catch (failure) {
                        if (!isNameConflict(failure)) throw failure;
                        if (next.kind === "blank" || attempt >= 4) throw new Error(takenSentence(name));
                        seen.add(name.toLowerCase());
                        name = freeName(next.name, seen);
                    }
                }
            }
            tick("made");
            const made = held.made;

            if (held.faceSaved === undefined) {
                const variant = variantFor(made.id, characterOf(next), characterForSeed) ?? 0;
                if (variant === 0) {
                    held.variant = 0;
                    held.faceSaved = true;
                } else {
                    try {
                        await source.setFace(made.id, identityIcon(variant));
                        held.variant = variant;
                        held.faceSaved = true;
                    } catch {
                        held.variant = 0;
                        held.faceSaved = false;
                    }
                }
            }
            tick("face");

            let asked: string | null = null;
            if (next.kind === "own") {
                if (held.planSaved === undefined) {
                    try {
                        await source.writePlan(made.id, startPlan(next.outcome, next.plan));
                        held.planSaved = true;
                    } catch {
                        held.planSaved = false;
                    }
                }
                tick("plan");
                asked = setupAsk(next.outcome);
                if (!held.conversationId) held.conversationId = await source.askToSetUp(made.id, asked);
                tick("ask");
            }

            await cache.invalidateQueries({ queryKey: [key, "lemmas", orgId] });
            const finished: Started = {
                made,
                variant: held.variant ?? 0,
                faceSaved: held.faceSaved ?? true,
                planSaved: next.kind === "own" ? (held.planSaved ?? false) : null,
                conversationId: held.conversationId ?? null,
                asked,
                needsModel: await model,
            };
            progress.current = null;
            return finished;
        } catch (failure) {
            /* What was made is real whether or not the rest followed, so the
               list shows it even if somebody chooses another start now. */
            if (held.made) void cache.invalidateQueries({ queryKey: [key, "lemmas", orgId] });
            setError(problemOf(failure, held.made
                ? `Couldn’t finish starting ${held.made.name}. Try again, and it carries on from where it stopped.`
                : "Couldn’t start it. Try again."));
            return null;
        } finally {
            setRunning(false);
        }
    }, [cache, key, orgId, source]);

    /** Back to choosing. What a failed start made is kept for a retry of the
     *  same start only. */
    const reset = useCallback(() => {
        setStart(null);
        setDone([]);
        setError(null);
    }, []);

    return { start, done, error, running, run, reset };
}

const joinKey = (key: string, podId: string) => moreKey(key, "begin", "join", podId);

/** Whether the signed-in person has asked to join this Lemma already. Not
 *  asked again on its own after a refusal: the answer would be the same. */
/** The organization a Lemma you can open is in, or null. */
export function useLemmaHome(podId: string) {
    const source = useBeginSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "more", "begin", "home", podId], queryFn: () => source.homeOf(podId), enabled: Boolean(podId), retry: false, staleTime: 60_000 });
}

export function useJoinStanding(podId: string) {
    const source = useBeginSource();
    const key = useCacheKey();
    return useQuery({ queryKey: joinKey(key, podId), queryFn: () => source.myJoinRequest(podId), enabled: Boolean(podId), retry: false });
}

/** Ask to join. An open door admits on the spot, and the Lemmas list is
 *  what decides whether it can be opened, so reading it again is walking in. */
export function useAskToJoin(podId: string) {
    const source = useBeginSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: () => source.askToJoin(podId),
        onSuccess: (request) => {
            cache.setQueryData(joinKey(key, podId), request);
            if (request.standing === "approved") void cache.invalidateQueries({ queryKey: [key, "lemmas"] });
        },
    });
}
