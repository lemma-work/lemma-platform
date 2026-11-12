import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey, useOrg } from "../../../data/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { computerPoll, lemmaModelSaid } from "./model";
import type { NewKey } from "./source";
import { firstProviderOffer } from "@web/org/provider-draft";
import { modelSetupState } from "@web/shell/runs-on-state";
import { describeChoice, type Choice, type Runtime } from "@web/data/runtimes";

/** This part's reads and writes, through react-query. Key every query with
 *  `adminKey(useCacheKey(), "models", …)`. */
export function useModelsSource() {
    return useAdminSource().models;
}

type OrgId = string | null | undefined;

const keys = {
    runtimes: (key: string, orgId: OrgId) => adminKey(key, "models", "runtimes", orgId),
    runtime: (key: string, orgId: OrgId, runtimeId: string | undefined) => adminKey(key, "models", "runtime", orgId, runtimeId),
    inherited: (key: string, orgId: OrgId) => adminKey(key, "models", "inherited", orgId),
    orgDefault: (key: string, orgId: OrgId) => adminKey(key, "models", "org-default", orgId),
    computers: (key: string) => adminKey(key, "models", "computers"),
    lemma: (key: string, podId: string) => adminKey(key, "models", "lemma", podId),
};

export function useRuntimes(orgId: OrgId) {
    const source = useModelsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: keys.runtimes(key, orgId), queryFn: () => source.runtimes(orgId!), enabled: Boolean(orgId), staleTime: 60_000 });
}

export function useInherited(orgId: OrgId) {
    const source = useModelsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: keys.inherited(key, orgId), queryFn: () => source.inherited(orgId!), enabled: Boolean(orgId), staleTime: 60_000 });
}

export function useOrgDefault(orgId: OrgId) {
    const source = useModelsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: keys.orgDefault(key, orgId), queryFn: () => source.organizationDefault(orgId!), enabled: Boolean(orgId), staleTime: 60_000 });
}

/** The computers and their agents, read again while the screen is open: a
 *  computer wakes, sleeps and finds agents on its own. */
export function useComputers(enabled: boolean) {
    const source = useModelsSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: keys.computers(key),
        queryFn: () => source.computers(),
        enabled,
        refetchInterval: (query) => computerPoll(query.state.data),
    });
}

/** Read every list this part feeds again, the chat's model picker's too, so
 *  a change here is what the next chat offers. */
function useRefresh(orgId: OrgId) {
    const cache = useQueryClient();
    const key = useCacheKey();
    return () => {
        void cache.invalidateQueries({ queryKey: keys.runtimes(key, orgId) });
        void cache.invalidateQueries({ queryKey: keys.inherited(key, orgId) });
        void cache.invalidateQueries({ queryKey: keys.orgDefault(key, orgId) });
        void cache.invalidateQueries({ queryKey: keys.computers(key) });
        void cache.invalidateQueries({ queryKey: [key, "runtimes", orgId] });
        void cache.invalidateQueries({ queryKey: [key, "default-runtime", orgId] });
    };
}

/** Read the models again by hand: the screen's Refresh. */
export function useRefreshModels(orgId: OrgId) {
    return useRefresh(orgId);
}

export function useSetOrgDefault(orgId: string) {
    const source = useModelsSource();
    const cache = useQueryClient();
    const key = useCacheKey();
    const refresh = useRefresh(orgId);
    return useMutation({
        mutationFn: (choice: Choice | null) => source.setOrganizationDefault(orgId, choice),
        onSuccess: (_answer, choice) => {
            cache.setQueryData(keys.orgDefault(key, orgId), choice);
            refresh();
        },
    });
}

export function useTestKey(orgId: string) {
    const source = useModelsSource();
    return useMutation({ mutationFn: (runtimeId: string) => source.test(orgId, runtimeId) });
}

export function useArchive(orgId: string) {
    const source = useModelsSource();
    const refresh = useRefresh(orgId);
    return useMutation({ mutationFn: (runtimeId: string) => source.archive(orgId, runtimeId), onSuccess: refresh });
}

export function useRestore(orgId: string) {
    const source = useModelsSource();
    const refresh = useRefresh(orgId);
    return useMutation({ mutationFn: (runtimeId: string) => source.restore(orgId, runtimeId), onSuccess: refresh });
}

/** Connect a key. Answers the key to offer as everyone's default when it is
 *  the first thing in the organization able to answer at all, and nobody has
 *  chosen a default (the web's `firstProviderOffer`); else null. */
export function useAddKey(orgId: string) {
    const source = useModelsSource();
    const cache = useQueryClient();
    const key = useCacheKey();
    const refresh = useRefresh(orgId);
    return useMutation({
        mutationFn: async (draft: NewKey): Promise<Runtime | null> => {
            const before = cache.getQueryData<Runtime[]>(keys.runtimes(key, orgId)) ?? await source.runtimes(orgId);
            await source.addKey(orgId, draft);
            const [after, chosen] = await Promise.all([source.runtimes(orgId), source.organizationDefault(orgId)]);
            cache.setQueryData(keys.runtimes(key, orgId), after);
            return firstProviderOffer(before, after, chosen);
        },
        onSuccess: refresh,
    });
}

/** One runtime's detail, for a chosen coding agent's options. */
export function useRuntimeDetail(orgId: OrgId, runtime: Runtime | undefined) {
    const source = useModelsSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: keys.runtime(key, orgId, runtime?.id),
        queryFn: () => source.runtime(orgId!, runtime!.id),
        enabled: Boolean(orgId) && runtime?.kind === "agent",
        staleTime: 60_000,
    });
}

/** What a Lemma runs on, read once for its screen and for its row on the
 *  Settings tab (the web's `useRunsOn`): the organization's list, what a
 *  Lemma that names nothing inherits, and the Lemma's own pick. */
export function useRunsOn(podId: string, orgId: OrgId) {
    const source = useModelsSource();
    const key = useCacheKey();
    const runtimes = useRuntimes(orgId);
    const inherited = useInherited(orgId);
    const current = useQuery({ queryKey: keys.lemma(key, podId), queryFn: () => source.lemmaChoice(podId), staleTime: 60_000 });

    /* Retired ones are never offered: a Lemma set to one could not run. */
    const live = useMemo(() => (runtimes.data ?? []).filter((one) => !one.archived), [runtimes.data]);
    const choice = current.data ?? null;
    const chosen = choice ? live.find((one) => one.id === choice.runtimeId) : undefined;
    const inheritedName = describeChoice(live, inherited.data ?? null);
    const setup = runtimes.isSuccess ? modelSetupState(live, inherited.isSuccess ? inherited.data : undefined, choice) : null;
    const said = lemmaModelSaid(live, choice, inherited.data ?? null, setup);
    return { runtimes, inherited, current, live, choice, chosen, inheritedName, setup, said };
}

/** Set what a Lemma runs on: shown at once, then read again, the chat's
 *  picker's read of it too. */
export function useSetLemmaChoice(podId: string) {
    const source = useModelsSource();
    const cache = useQueryClient();
    const key = useCacheKey();
    return useMutation({
        mutationFn: (choice: Choice | null) => source.setLemmaChoice(podId, choice),
        onSuccess: (_answer, choice) => {
            cache.setQueryData(keys.lemma(key, podId), choice);
            void cache.invalidateQueries({ queryKey: keys.lemma(key, podId) });
            void cache.invalidateQueries({ queryKey: [key, "pod-runtime", podId] });
        },
    });
}

/** The row's live line on a Lemma's Settings tab: what it runs on now, with
 *  the violet dot when there is nothing to run on (the web's
 *  `useRunsOn(...).said`). `text` null while it is read, or when it could
 *  not be. */
export function useModelLine(podId: string): AboutLine {
    const { org } = useOrg();
    const runsOn = useRunsOn(podId, org?.id);
    if (!org) return { text: null };
    return {
        text: runsOn.current.isSuccess && runsOn.runtimes.isSuccess ? runsOn.said : null,
        needs: Boolean(runsOn.setup),
    };
}
