import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSession, useSource } from "../session/session";
import { store } from "../session/store";
import { DAY_MS, chatRows, freshQueue, needItems, sectionRows, sidebarSections, startedByLemma, timeOf, workRows } from "./model";
import type { Lemma, LemmaSection, NeedItem, Org, Owns } from "./types";

/** The screens' reads, through react-query, from whichever source the
 *  session picked. Keyed by the source's label and the server, so sample
 *  data and a live account never share a cache entry. */

const POLL_MS = 60_000;

/** The cache key every query starts with: "sample", or the server. */
export function useCacheKey(): string {
    const { mode, server } = useSession();
    return mode === "sample" ? "sample" : server.id;
}

export function useMe() {
    const source = useSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "me"], queryFn: () => source.me(), staleTime: 5 * 60_000 });
}

/** The organization the app is in: the one chosen last, else the first. */
export function useOrg(): { org: Org | null; orgs: Org[]; pick: (id: string) => void; pending: boolean; error: unknown } {
    const source = useSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const orgs = useQuery({ queryKey: [key, "orgs"], queryFn: () => source.orgs(), staleTime: 10 * 60_000 });
    const chosen = useQuery({ queryKey: ["org-choice"], queryFn: async () => (await store.org()) ?? "", staleTime: Infinity });
    const list = orgs.data ?? [];
    const org = list.find((one) => one.id === chosen.data) ?? list[0] ?? null;
    const pick = useCallback((id: string) => {
        void store.setOrg(id).then(() => cache.setQueryData(["org-choice"], id));
    }, [cache]);
    return { org, orgs: list, pick, pending: orgs.isPending, error: orgs.error };
}

export function useLemmas() {
    const source = useSource();
    const key = useCacheKey();
    const { org, pending: orgsPending } = useOrg();
    const query = useQuery({
        queryKey: [key, "lemmas", org?.id],
        queryFn: () => source.lemmas(org!.id),
        enabled: Boolean(org),
        staleTime: 5 * 60_000,
    });
    /* No organization is not loading: it is a person who has not set one up. */
    return { lemmas: query.data ?? [], pending: org ? query.isPending : orgsPending, error: query.error, org, refetch: query.refetch };
}

/** Each Lemma's raw reads, polled once a minute: nothing pushes a new ask,
 *  so a list that only read on navigation could sit saying nothing is owed. */
export function usePulse() {
    const source = useSource();
    const key = useCacheKey();
    const { lemmas, org } = useLemmas();
    const query = useQuery({
        queryKey: [key, "pulse", org?.id, lemmas.map((one) => one.id).join(",")],
        queryFn: () => source.pulse(org!.id, lemmas),
        enabled: Boolean(org) && lemmas.length > 0,
        staleTime: 20_000,
        refetchInterval: POLL_MS,
    });
    return { pulse: query.data ?? [], pending: query.isPending && lemmas.length > 0, refetch: query.refetch, fetching: query.isFetching };
}

export function useOwnsAll(lemmas: readonly Lemma[]): Map<string, Owns | undefined> {
    const source = useSource();
    const key = useCacheKey();
    const results = useQueries({
        queries: lemmas.map((lemma) => ({
            queryKey: [key, "owns", lemma.id],
            queryFn: () => source.owns(lemma),
            staleTime: 5 * 60_000,
        })),
    });
    return new Map(lemmas.map((lemma, index) => [lemma.id, results[index]?.data]));
}

export function useOwns(lemma: Lemma | null) {
    const source = useSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "owns", lemma?.id], queryFn: () => source.owns(lemma!), enabled: Boolean(lemma), staleTime: 5 * 60_000 });
}

/** Everything waiting on the reader, every Lemma, oldest first. */
export function useNeeds(): { needs: NeedItem[]; pending: boolean; refetch: () => void } {
    const { pulse, pending, refetch } = usePulse();
    const needs = useMemo(() => {
        const now = Date.now();
        return freshQueue(pulse.flatMap((one) => needItems(one.lemma, one.asks, one.waits, one.contacts, now)), now);
    }, [pulse]);
    return { needs, pending, refetch: () => void refetch() };
}

/** The Lemmas tab: each Lemma's section, busiest first, quiet ones folded. */
export function useSections(): { sections: LemmaSection[]; pending: boolean; refetch: () => void; toggle: (id: string, open: boolean) => void } {
    const { pulse, pending, refetch } = usePulse();
    const { lemmas } = useLemmas();
    const owns = useOwnsAll(lemmas);
    const [chosen, setChosen] = useState<Map<string, boolean>>(new Map());
    useEffect(() => {
        void store.folded().then(setChosen);
    }, []);
    const toggle = useCallback((id: string, open: boolean) => {
        setChosen((was) => {
            const next = new Map(was);
            next.set(id, open);
            void store.setFolded(next);
            return next;
        });
    }, []);
    const sections = useMemo(() => {
        const now = Date.now();
        const built = sidebarSections(pulse.map((one) => ({
            lemma: one.lemma,
            rows: sectionRows(chatRows(one.lemma.id, one.conversations, one.channels), workRows({ podId: one.lemma.id, conversations: one.conversations, tables: one.tables, now })),
        })), now, chosen);
        return built.map((section) => ({ ...section, owns: owns.get(section.lemma.id) ?? null }));
    }, [pulse, chosen, owns]);
    return { sections, pending, refetch: () => void refetch(), toggle };
}

/** "since last night": when the reader last looked, said as a person would. */
export function sinceWords(lastSeen: number | null, now: number): string {
    if (lastSeen === null) return "in the last day";
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    if (lastSeen >= midnight.getTime()) return "since you were last here";
    if (midnight.getTime() - lastSeen < 12 * 3_600_000) return "since last night";
    if (midnight.getTime() - lastSeen < DAY_MS) return "since yesterday";
    return "since " + new Date(lastSeen).toLocaleDateString("en-US", { weekday: "long" });
}

/** Home's line under the headline: runs the Lemmas started on their own
 *  since the reader last looked. Read once; marked seen a few seconds after
 *  arriving, so passing through does not reset it. */
export function useStartedOnTheirOwn(): { count: number; since: string } {
    const source = useSource();
    const key = useCacheKey();
    const { org } = useOrg();
    const { pulse } = usePulse();
    const seen = useQuery({ queryKey: [key, "home-seen", org?.id], queryFn: () => source.homeSeen(org!.id), enabled: Boolean(org), staleTime: Infinity });
    useEffect(() => {
        if (!org || seen.isPending) return;
        const timer = setTimeout(() => void source.markHomeSeen(org.id, Date.now()), 4000);
        return () => clearTimeout(timer);
    }, [org, seen.isPending, source]);
    return useMemo(() => {
        const now = Date.now();
        const lastSeen = seen.data ?? null;
        const from = lastSeen ?? now - DAY_MS;
        let count = 0;
        for (const one of pulse) {
            for (const conversation of one.conversations) {
                const at = timeOf(conversation.activityAt);
                if (at !== null && at >= from && startedByLemma(conversation)) count += 1;
            }
        }
        return { count, since: sinceWords(lastSeen, now) };
    }, [pulse, seen.data]);
}

export function useCard(item: NeedItem) {
    const source = useSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "card", item.key, item.toolCallId], queryFn: () => source.card(item), staleTime: 60_000 });
}

/** Answer in place; the queue reads again so the card leaves. */
export function useAnswer() {
    const source = useSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: ({ item, decision, answers }: { item: NeedItem; decision: "APPROVE_ONCE" | "DENY"; answers?: Record<string, unknown> }) => source.answer(item, decision, answers),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: [key, "pulse"] });
        },
    });
}

export function useApps() {
    const source = useSource();
    const key = useCacheKey();
    const { lemmas, org } = useLemmas();
    const query = useQuery({
        queryKey: [key, "apps", org?.id, lemmas.map((one) => one.id).join(",")],
        queryFn: () => source.apps(org!.id, lemmas),
        enabled: Boolean(org) && lemmas.length > 0,
        staleTime: 5 * 60_000,
    });
    return { apps: query.data ?? [], pending: query.isPending && lemmas.length > 0, refetch: query.refetch };
}

export function useRuns(lemma: Lemma | null) {
    const source = useSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "runs", lemma?.id], queryFn: () => source.runs(lemma!), enabled: Boolean(lemma), staleTime: 5 * 60_000 });
}

export function useStarters(lemma: Lemma | null) {
    const source = useSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "starters", lemma?.id], queryFn: () => source.starters(lemma!), enabled: Boolean(lemma), staleTime: 5 * 60_000 });
}

export function useLines(podId: string, channelId: string) {
    const source = useSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "lines", podId, channelId], queryFn: () => source.lines(podId, channelId), refetchInterval: 15_000 });
}

export function useSay(podId: string, channelId: string) {
    const source = useSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (text: string) => source.say(podId, channelId, text),
        onSuccess: () => void cache.invalidateQueries({ queryKey: [key, "lines", podId, channelId] }),
    });
}

export function useLemma(podId: string): Lemma | null {
    const { lemmas } = useLemmas();
    return lemmas.find((one) => one.id === podId) ?? null;
}

/** The source key, for screens that read something of their own. */
export function useSourceKey(): string {
    return useCacheKey();
}
