import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useIsFocused } from "expo-router";
import { useCacheKey, useMe, useOrg } from "../../../data/hooks";
import type { Lemma, Org } from "../../../data/types";
import { useSource } from "../../../session/session";
import { connectorChoices, type ConnectorPick } from "@web/site/import/plan";
import { problemOf } from "../../admin/words";
import { moreKey, useMoreSource } from "../hooks";
import { NEW, answersOf, isWorking, personalName, whoOf } from "./model";
import type { ImportStatusResponse, PendingInstall } from "./source";

/** This part's reads and writes. Every key of its own is
 *  `moreKey(useCacheKey(), "install", …)`; the organizations and an
 *  organization's Lemmas are the app's own (`[cacheKey, "orgs"]`,
 *  `[cacheKey, "lemmas", orgId]`, as `data/hooks.ts` keys them), so a Lemma
 *  made here shows in every list. */
export function useInstallSource() {
    return useMoreSource().install;
}

const PART = "install";

/** A repository's public README. Read once a session: it changes on a push,
 *  not while somebody is deciding. */
export function useReadme(owner: string, repo: string, enabled = true) {
    const source = useInstallSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: moreKey(key, PART, "readme", owner.toLowerCase(), repo.toLowerCase()),
        queryFn: () => source.readme(owner, repo),
        enabled,
        staleTime: 30 * 60_000,
        retry: false,
    });
}

/** An organization's Lemmas: the same read, under the same key, as
 *  `useLemmas` makes for the chosen organization. */
export function useOrgLemmas(orgId: string | null) {
    const source = useSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: [key, "lemmas", orgId],
        queryFn: () => source.lemmas(orgId!),
        enabled: Boolean(orgId),
        staleTime: 5 * 60_000,
    });
}

/** An organization chosen on this screen, else the app's own. Choosing here
 *  changes nothing elsewhere until the person opens what was made. */
export function useChosenOrg() {
    const { org, orgs, pending, error, pick } = useOrg();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [chosen, setChosen] = useState<string | null>(null);
    const here = orgs.find((one) => one.id === chosen) ?? org;
    const reread = useCallback(() => void cache.invalidateQueries({ queryKey: [key, "orgs"] }), [cache, key]);
    /** Open what was made in its own organization: the app's lists are the
     *  chosen organization's, so a Lemma in another one would not be found. */
    const arriveIn = useCallback((orgId: string | null) => {
        if (orgId && orgId !== org?.id && orgs.some((one) => one.id === orgId)) pick(orgId);
    }, [org?.id, orgs, pick]);
    return { org: here, orgs, pending, error, choose: setChosen, reread, arriveIn };
}

/** A first organization for somebody with none, named after them, and the
 *  journey carrying on in it (the web's `FirstWorkspace`). */
export function useFirstOrg() {
    const source = useInstallSource();
    const data = useSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMe();
    const { pick } = useOrg();
    const suggested = personalName(me.data);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const make = useCallback(async (typed: string) => {
        setBusy(true);
        setError("");
        try {
            const name = typed.trim();
            /* Untouched, the name is ours, and a second Ada gets an
               organization rather than a complaint about a name she never
               chose. */
            const made = await source.createOrg(name || suggested, !name || name === suggested);
            const orgs = await cache.fetchQuery<Org[]>({ queryKey: [key, "orgs"], queryFn: () => data.orgs(), staleTime: 0 });
            /* Only an organization the list holds is kept as the choice. */
            if (orgs.some((one) => one.id === made.id)) pick(made.id);
        } catch (failure) {
            setError(problemOf(failure, "Your organization could not be made. Try again."));
        } finally {
            setBusy(false);
        }
    }, [cache, data, key, pick, source, suggested]);
    return { suggested, make, busy, error };
}

/** The install somebody started and has not finished, and the way to stop
 *  offering it. */
export function usePendingInstall(): { held: PendingInstall | null; dismiss: () => void } {
    const source = useInstallSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const query = useQuery({ queryKey: moreKey(key, PART, "pending"), queryFn: () => source.pending(), staleTime: 0 });
    const dismiss = useCallback(() => {
        cache.setQueryData(moreKey(key, PART, "pending"), null);
        void source.forgetPending();
    }, [cache, key, source]);
    return { held: query.data ?? null, dismiss };
}

/** Written down when the install screen opens, before anything can send the
 *  person away, so the app can offer it back (`ResumeInstall`). */
export function useRememberInstall(install: PendingInstall | null) {
    const source = useInstallSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const path = install?.path ?? null;
    const title = install?.title ?? null;
    useEffect(() => {
        if (!path || !title) return;
        void source.rememberPending({ path, title }).then(() => cache.invalidateQueries({ queryKey: moreKey(key, PART, "pending") }));
    }, [cache, key, path, source, title]);
}

export type Busy = "review" | "install" | "back" | "stop" | null;

/** The install journey: Choose, Review, Install (the web's `Journey` in
 *  `install-panel.tsx`, the same calls in the same order). Its state lives
 *  with the screen, so closing the sheet it is drawn in loses nothing, and
 *  the job is read every 1.2 s while it works and the screen is on top. */
export function useJourney({ owner, repo, title }: { owner: string; repo: string; title: string }) {
    const source = useInstallSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const focused = useIsFocused();
    const orgs = useChosenOrg();
    const orgId = orgs.org?.id ?? null;
    const lemmas = useOrgLemmas(orgId);

    const [target, setTarget] = useState<string>(NEW);
    const [typed, setTyped] = useState<string | null>(null);
    /* The Lemma this screen made for the install. Kept, so going back to the
       choice and on again uses it rather than making a second. */
    const [created, setCreated] = useState<Lemma | null>(null);
    const [job, setJob] = useState<ImportStatusResponse | null>(null);
    const [values, setValues] = useState<Record<string, string>>({});
    /* What the person picked for each connector the bundle brings, by slug. */
    const [picks, setPicks] = useState<Record<string, ConnectorPick>>({});
    const [confirm, setConfirm] = useState(false);
    const [busy, setBusy] = useState<Busy>(null);
    const [error, setError] = useState("");
    /* Reading the job failed: polling waits for "Check again". */
    const [lost, setLost] = useState(false);
    const [details, setDetails] = useState(false);

    const name = typed ?? title;
    const list = useMemo(() => lemmas.data ?? [], [lemmas.data]);
    const who = whoOf(target, name, created, list);
    const chosenLemma = target === NEW ? created : list.find((one) => one.id === target) ?? null;

    const working = isWorking(job);
    useEffect(() => {
        if (!job || !working || !focused || lost) return;
        let alive = true;
        const timer = setTimeout(() => {
            source.readImport(job.pod_id, job.import_id).then(
                (next) => { if (alive) setJob(next); },
                (failure: unknown) => {
                    if (!alive) return;
                    setLost(true);
                    setError(problemOf(failure, "Lost track of the installation."));
                },
            );
        }, 1200);
        return () => {
            alive = false;
            clearTimeout(timer);
        };
    }, [job, working, focused, lost, source]);

    const finished = job?.status === "COMPLETED";
    const finishedIn = finished ? job?.pod_id ?? null : null;
    useEffect(() => {
        if (!finishedIn) return;
        void source.forgetPending().then(() => cache.invalidateQueries({ queryKey: moreKey(key, PART, "pending") }));
        /* What it brought shows wherever the app lists it. */
        void cache.invalidateQueries({ queryKey: [key, "apps"] });
        void cache.invalidateQueries({ queryKey: [key, "lemmas"] });
        void cache.invalidateQueries({ queryKey: [key, "runs", finishedIn] });
        void cache.invalidateQueries({ queryKey: [key, "pulse"] });
    }, [cache, finishedIn, key, source]);

    /** The organization's own GitHub account, when it has a usable one.
     *  Without one the fetch is anonymous and GitHub rate-limits it; failing
     *  to read the list only means the slower path. */
    const chooseOrg = useCallback((id: string) => {
        orgs.choose(id);
        setTarget(NEW);
        setCreated(null);
    }, [orgs]);

    const review = useCallback(async () => {
        if (!orgId) return;
        setBusy("review");
        setError("");
        try {
            let id = target === NEW ? created?.id : target;
            if (!id) {
                const made = await source.createLemma(orgId, name.trim());
                setCreated(made);
                id = made.id;
                void cache.invalidateQueries({ queryKey: [key, "lemmas", orgId] });
            }
            /* Fetched through the Lemma's `github` connector as the person
               installing, or anonymously for a public repository. */
            setJob(await source.startImport(id, { owner, repo }));
            setLost(false);
        } catch (failure) {
            setError(problemOf(failure, `Could not read what ${title} includes.`));
        } finally {
            setBusy(null);
        }
    }, [cache, created, key, name, orgId, owner, repo, source, target, title]);

    const install = useCallback(async () => {
        if (!job?.plan) return;
        setBusy("install");
        setError("");
        try {
            setJob(await source.applyImport(job.pod_id, job.import_id, {
                variables: answersOf(job.plan.variables, values, who),
                connectors: connectorChoices(job.plan.connectors ?? [], picks),
                confirmDestructive: confirm,
            }));
            setLost(false);
        } catch (failure) {
            setError(problemOf(failure, "The installation failed."));
        } finally {
            setBusy(null);
        }
    }, [confirm, job, picks, source, values, who]);

    /** Back to the choice. A job still waiting on review is withdrawn so it
     *  does not sit open against the Lemma. */
    const back = useCallback(async () => {
        if (job && job.status === "AWAITING_CONFIRMATION") {
            setBusy("back");
            try {
                await source.cancelImport(job.pod_id, job.import_id);
            } catch {
                /* Leaving it open is harmless; a new review starts a new job. */
            } finally {
                setBusy(null);
            }
        }
        setJob(null);
        setConfirm(false);
        setError("");
        setLost(false);
        setDetails(false);
    }, [job, source]);

    const stop = useCallback(async () => {
        if (!job) return;
        setBusy("stop");
        try {
            await source.cancelImport(job.pod_id, job.import_id);
            /* What had been installed stays: say so rather than "nothing". */
            setJob({ ...job, status: job.progress.done > 0 ? "PARTIALLY_CANCELLED" : "CANCELLED" });
            setError("");
        } catch (failure) {
            setError(problemOf(failure, "Could not stop it."));
        } finally {
            setBusy(null);
        }
    }, [job, source]);

    const checkAgain = useCallback(() => {
        setError("");
        setLost(false);
        /* A fresh object, so the poll is scheduled again. */
        setJob((was) => (was ? { ...was } : was));
    }, []);

    return {
        orgs,
        orgId,
        lemmas,
        list: list.filter((one) => one.id !== created?.id),
        target,
        setTarget,
        name,
        setName: setTyped,
        created,
        chosenLemma,
        who,
        job,
        values,
        setValue: (variable: string, value: string) => setValues((was) => ({ ...was, [variable]: value })),
        picks,
        setPick: (slug: string, pick: ConnectorPick) => setPicks((was) => ({ ...was, [slug]: pick })),
        confirm,
        setConfirm,
        busy,
        error,
        lost,
        working,
        details,
        setDetails,
        chooseOrg,
        review,
        install,
        back,
        stop,
        checkAgain,
    };
}

export type Journey = ReturnType<typeof useJourney>;

/** Remix: a Lemma to take the app (made if new, and kept once made), then a
 *  new chat in it with the words in the box. */
export function useRemix() {
    const source = useInstallSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const orgs = useChosenOrg();
    const orgId = orgs.org?.id ?? null;
    const lemmas = useOrgLemmas(orgId);
    const [target, setTarget] = useState<string>(NEW);
    const [created, setCreated] = useState<Lemma | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");

    const chooseOrg = useCallback((id: string) => {
        orgs.choose(id);
        setTarget(NEW);
        setCreated(null);
    }, [orgs]);

    /** The Lemma the remix goes to, made now if it is a new one. */
    const prepare = useCallback(async (name: string): Promise<string | null> => {
        if (!orgId) return null;
        setBusy(true);
        setError("");
        try {
            let id = target === NEW ? created?.id : target;
            if (!id) {
                const made = await source.createLemma(orgId, name.trim());
                setCreated(made);
                id = made.id;
                void cache.invalidateQueries({ queryKey: [key, "lemmas", orgId] });
            }
            orgs.arriveIn(orgId);
            return id;
        } catch (failure) {
            setError(problemOf(failure, "Could not start the remix."));
            return null;
        } finally {
            setBusy(false);
        }
    }, [cache, created, key, orgId, orgs, source, target]);

    return {
        orgs,
        orgId,
        lemmas,
        list: (lemmas.data ?? []).filter((one) => one.id !== created?.id),
        target,
        setTarget: (next: string) => setTarget(next),
        created,
        busy,
        error,
        chooseOrg,
        prepare,
    };
}
