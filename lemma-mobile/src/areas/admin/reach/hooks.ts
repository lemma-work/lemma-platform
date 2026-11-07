import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SurfaceUpdateRequest } from "lemma-sdk";
import { useCacheKey } from "../../../data/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { ownPlaces, placeNeedsYou, reachLine } from "./places";

/** This part's reads and writes, through react-query. Every key is
 *  `adminKey(useCacheKey(), "reach", …)`; everything about one Lemma sits
 *  under `[…, "reach", podId]`, so a change reads all of it again. */
export function useReachSource() {
    return useAdminSource().reach;
}

function useKey() {
    const key = useCacheKey();
    return (...rest: readonly unknown[]) => adminKey(key, "reach", ...rest);
}

/** The places a Lemma answers. */
export function usePlaces(podId: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "places"), queryFn: () => source.places(podId), staleTime: 60_000, enabled: Boolean(podId) });
}

/** The places it could answer, cheapest first. */
export function useCatalog(podId: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "catalog"), queryFn: () => source.catalog(podId), staleTime: 5 * 60_000, enabled: Boolean(podId) });
}

/** The Lemma's connectors: a SHARED one is a way to answer at once. */
export function usePodConnectors(podId: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "pod-connectors"), queryFn: () => source.podConnectors(podId), enabled: Boolean(podId), staleTime: 60_000 });
}

export function useOrgAccounts(orgId: string | null | undefined) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key("accounts", orgId), queryFn: () => source.accounts(orgId!), enabled: Boolean(orgId), staleTime: 60_000 });
}

/** Where the reader's Lemmas answer, across the organization. */
export function useMine() {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key("mine"), queryFn: () => source.mine(), staleTime: 60_000 });
}

/** Read everything about a Lemma's places again, and the organization's
 *  accounts and claims with it: a connection changes all three. */
export function useRefreshReach(podId: string, orgId?: string | null) {
    const cache = useQueryClient();
    const cacheKey = useCacheKey();
    return useCallback(() => {
        void cache.invalidateQueries({ queryKey: adminKey(cacheKey, "reach", podId) });
        void cache.invalidateQueries({ queryKey: adminKey(cacheKey, "reach", "mine") });
        if (orgId) void cache.invalidateQueries({ queryKey: adminKey(cacheKey, "reach", "accounts", orgId) });
        void cache.invalidateQueries({ queryKey: adminKey(cacheKey, "reach", podId, "pod-connectors") });
        /* The chats lists show a Lemma's groups. */
        void cache.invalidateQueries({ queryKey: [cacheKey, "pulse"] });
    }, [cache, cacheKey, podId, orgId]);
}

/** Answer on Lemma's own identity, or on an account the organization holds. */
export function useConnect(podId: string, orgId?: string | null) {
    const source = useReachSource();
    const refresh = useRefreshReach(podId, orgId);
    return useMutation({
        mutationFn: (way: { platform: string; podConnector?: string | null }) => (way.podConnector
            ? source.connectPodConnector(podId, way.platform, way.podConnector)
            : source.connectSystem(podId, way.platform)),
        onSuccess: refresh,
    });
}

export function useDisconnect(podId: string, orgId?: string | null) {
    const source = useReachSource();
    const refresh = useRefreshReach(podId, orgId);
    return useMutation({ mutationFn: (name: string) => source.disconnect(podId, name), onSuccess: refresh });
}

/** One place's settings. */
export function usePlaceDetail(podId: string, name: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "detail", name), queryFn: () => source.detail(podId, name), staleTime: 30_000 });
}

/** Whether it works and what is left; never kept, since it is a live check. */
export function usePlaceSetup(podId: string, name: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "setup", name), queryFn: () => source.setup(podId, name), gcTime: 0 });
}

export function usePlaceOptions(podId: string, name: string, enabled: boolean) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "options", name), queryFn: () => source.options(podId, name), enabled, staleTime: 30_000 });
}

export function useAnswerers(podId: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "answerers"), queryFn: () => source.answerers(podId), staleTime: 5 * 60_000 });
}

export function useSavePlace(podId: string, name: string) {
    const source = useReachSource();
    const refresh = useRefreshReach(podId);
    return useMutation({ mutationFn: (patch: SurfaceUpdateRequest) => source.update(podId, name, patch), onSuccess: refresh });
}

/** The groups a place is in. `watch` reads them every few seconds, for a
 *  sheet waiting on a group to arrive. */
export function useGroups(podId: string, name: string, enabled = true, watch = false) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({
        queryKey: key(podId, "groups", name),
        queryFn: () => source.groups(podId, name),
        enabled,
        staleTime: 30_000,
        refetchInterval: watch ? 3000 : false,
    });
}

export function useJoinGroup(podId: string) {
    const source = useReachSource();
    const refresh = useRefreshReach(podId);
    return useMutation({ mutationFn: (channelId: string) => source.joinGroup(podId, channelId), onSuccess: refresh });
}

export function useSlackManaged(podId: string, enabled = true) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "slack-managed"), queryFn: () => source.slackManaged(podId), enabled });
}

export function useSlackAppWorkspaces(podId: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key(podId, "slack-apps"), queryFn: () => source.slackAppWorkspaces(podId) });
}

export function useSlackManifest(name: string) {
    const source = useReachSource();
    const key = useKey();
    return useQuery({ queryKey: key("slack-manifest", name), queryFn: () => source.slackManifest(name), staleTime: 10 * 60_000 });
}

/** The row's live line on a Lemma's Settings tab: the places it is reached
 *  on now, else only in Lemma; the violet dot when one of its own places
 *  waits on somebody to approve or finish it. */
export function useChannelsLine(podId: string): AboutLine {
    const places = usePlaces(podId);
    if (!places.isSuccess) return { text: null };
    return { text: reachLine(places.data), needs: ownPlaces(places.data).some(placeNeedsYou) };
}
