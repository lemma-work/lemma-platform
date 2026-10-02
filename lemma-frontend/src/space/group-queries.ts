"use client";

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import type { GroupUpdateRequest } from "lemma-sdk";
import { source, type Group, type GroupDetail } from "@/data";

/** The keys every view of a space's groups reads under, so a change made on
 *  one — the Groups page, a group's page, a bot's channel list — is the
 *  change all of them show. */
export const groupsKey = (podId: string) => ["groups", podId] as const;
export const groupKey = (podId: string, groupId: string) => ["group", podId, groupId] as const;
export const timelineKey = (podId: string, groupId: string) => ["group-timeline", podId, groupId] as const;

/** Every group the space's bots are in. `poll` keeps asking while a sheet
 *  waits for a group to arrive from Telegram or Slack. */
export function useGroups(podId: string, poll: number | false = false, enabled = true) {
    return useQuery({
        queryKey: groupsKey(podId),
        enabled,
        queryFn: () => source.listGroups(podId),
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        refetchInterval: poll,
    });
}

/** After a group changed somewhere nothing wrote it back: read every list
 *  that shows it again. */
export function refreshGroups(cache: QueryClient, podId: string): void {
    void cache.invalidateQueries({ queryKey: ["groups", podId] });
    void cache.invalidateQueries({ queryKey: ["group", podId] });
    void cache.invalidateQueries({ queryKey: ["surface-groups", podId] });
}

/** The group as saved, written into the list and the open page, which carry
 *  the same row. The page keeps its people and what is waiting — the save
 *  answers neither. */
export function keepSavedGroup(cache: QueryClient, podId: string, saved: Group): void {
    cache.setQueryData<Group[]>(groupsKey(podId), (was) => was?.map((group) => (group.id === saved.id ? saved : group)));
    cache.setQueryData<GroupDetail>(groupKey(podId, saved.id), (was) => (was ? { ...was, ...saved, people: was.people, waiting: was.waiting } : was));
    void cache.invalidateQueries({ queryKey: ["surface-groups", podId] });
}

/** Switch people outside on or off in a group, or take it on. */
export function useGroupChange(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: ({ groupId, change }: { groupId: string; change: GroupUpdateRequest }) =>
            source.updateGroup(podId, groupId, change),
        onSuccess: (saved) => keepSavedGroup(cache, podId, saved),
    });
}
