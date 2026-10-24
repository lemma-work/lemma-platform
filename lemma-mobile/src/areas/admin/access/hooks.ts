import { useQuery } from "@tanstack/react-query";
import { useCacheKey, useMe } from "../../../data/hooks";
import { adminKey, useAdminSource } from "../hooks";

/** The actions the signed-in person holds in a Lemma. */
export function usePodActions(podId: string) {
    const source = useAdminSource().access;
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "access", podId, "actions"), queryFn: () => source.podActions(podId), staleTime: 60_000, enabled: Boolean(podId) });
}

/** Whether the server lets the signed-in person do this in a Lemma.
 *  `allowed` is false until the server has said yes; `answered` is whether it
 *  has said anything, so a "you can't" line is not drawn while it is asked. */
export function useMayI(podId: string, action: string): { allowed: boolean; answered: boolean } {
    const actions = usePodActions(podId);
    return { allowed: (actions.data ?? []).includes(action), answered: actions.isSuccess };
}

/** The organization's members, under one key for every part that reads them. */
export function useOrgMembers(orgId: string | null | undefined) {
    const source = useAdminSource().access;
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "access", "org-members", orgId), queryFn: () => source.orgMembers(orgId!), enabled: Boolean(orgId), staleTime: 60_000 });
}

/** What the signed-in person is in the organization (`ORG_OWNER`, …), found
 *  the way the web finds it: their row in the members list. `role` is null
 *  while that is read, or when it cannot be told; nothing is withheld on a
 *  guess, and the server still refuses what it must. */
export function useMyOrgRole(orgId: string | null | undefined): { role: string | null; answered: boolean } {
    const me = useMe();
    const members = useOrgMembers(orgId);
    const mine = (members.data ?? []).find((member) => member.user_id && member.user_id === me.data?.id);
    return { role: mine?.role ?? null, answered: members.isSuccess && me.isSuccess };
}

/** "local" on an installation on somebody's own computer, which has no
 *  billing; null until known. */
export function useDeployment(): string | null {
    const source = useAdminSource().access;
    const key = useCacheKey();
    const answer = useQuery({ queryKey: adminKey(key, "access", "deployment"), queryFn: () => source.deployment(), staleTime: 30 * 60_000 });
    return answer.data ?? null;
}
