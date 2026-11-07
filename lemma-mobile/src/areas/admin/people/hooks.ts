import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { useMayI } from "../access/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { MANAGE_MEMBERS } from "@web/data/pod-roles";
import { peopleLine } from "./model";
import type { JoinPolicy, JoinRequest } from "@web/data/joining";

/** This part's reads and writes, through react-query. */
export function usePeopleSource() {
    return useAdminSource().people;
}

function useKey(podId: string, ...rest: readonly unknown[]) {
    return adminKey(useCacheKey(), "people", podId, ...rest);
}

/** Everyone with access to a Lemma. Also what the MCP part names a
 *  connection's owner from. */
export function usePodMembers(podId: string) {
    const source = usePeopleSource();
    const key = useKey(podId, "members");
    return useQuery({ queryKey: key, queryFn: () => source.members(podId), staleTime: 60_000, enabled: Boolean(podId) });
}

/** Whether the server lets the signed-in person change who has access here:
 *  add, invite, move the door, let somebody in. Unknown counts as no. */
export function useMayManage(podId: string) {
    return useMayI(podId, MANAGE_MEMBERS);
}

/** The invitations waiting on this Lemma, read only for somebody who may add
 *  people, as on the web: listing them is theirs to do. */
export function usePendingInvites(podId: string, orgId: string | null | undefined, allowed: boolean) {
    const source = usePeopleSource();
    const key = useKey(podId, "invitations", orgId);
    return useQuery({ queryKey: key, queryFn: () => source.invitations(orgId!, podId), enabled: allowed && Boolean(orgId), staleTime: 60_000, retry: false });
}

/** Give somebody in the organization access. The roster is read again: the
 *  person appearing in it is the confirmation. */
export function useAddPerson(podId: string) {
    const source = usePeopleSource();
    const cache = useQueryClient();
    const members = useKey(podId, "members");
    return useMutation({
        mutationFn: ({ orgMemberId, role }: { orgMemberId: string; role: string }) => source.add(podId, orgMemberId, role),
        onSuccess: () => void cache.invalidateQueries({ queryKey: members }),
    });
}

/** Invite somebody new to the organization, into this Lemma. */
export function useInvitePerson(podId: string, orgId: string | null | undefined) {
    const source = usePeopleSource();
    const cache = useQueryClient();
    const invitations = useKey(podId, "invitations", orgId);
    return useMutation({
        mutationFn: ({ email, role }: { email: string; role: string }) => source.invite(orgId!, podId, email, role),
        onSuccess: () => void cache.invalidateQueries({ queryKey: invitations }),
    });
}

export function useJoinPolicy(podId: string) {
    const source = usePeopleSource();
    const key = useKey(podId, "join");
    return useQuery({ queryKey: key, queryFn: () => source.joinPolicy(podId), staleTime: 60_000 });
}

/** Move the door. The answer on screen is the one being saved while it is
 *  (the screen reads `variables`), and the server's after. */
export function useSetJoinPolicy(podId: string) {
    const source = usePeopleSource();
    const cache = useQueryClient();
    const key = useKey(podId, "join");
    return useMutation({
        mutationFn: (policy: JoinPolicy) => source.setJoinPolicy(podId, policy),
        onSuccess: (_answer, policy) => {
            cache.setQueryData(key, policy);
            void cache.invalidateQueries({ queryKey: key });
        },
    });
}

/** Who is asking to be let in. Not retried: most people may not list it,
 *  and a refusal is the ordinary answer, not a fault. */
export function useJoinRequests(podId: string) {
    const source = usePeopleSource();
    const key = useKey(podId, "requests");
    return useQuery({ queryKey: key, queryFn: () => source.joinRequests(podId), staleTime: 30_000, retry: false });
}

/** Let one in: their ask leaves the queue at once, and the roster, which
 *  has truly changed, is read again. */
export function useAdmit(podId: string) {
    const source = usePeopleSource();
    const cache = useQueryClient();
    const requests = useKey(podId, "requests");
    const members = useKey(podId, "members");
    return useMutation({
        mutationFn: (request: JoinRequest) => source.admit(podId, request.id),
        onSuccess: (_answer, request) => {
            cache.setQueryData<JoinRequest[]>(requests, (before) => (before ?? []).filter((one) => one.id !== request.id));
            void cache.invalidateQueries({ queryKey: members });
        },
    });
}

/** The row's live line on a Lemma's Settings tab: "3 people · 1 asking to
 *  join", with the dot while somebody is asking. A refused queue counts as
 *  nobody asking, as it reads for anyone who may not list it. */
export function usePeopleLine(podId: string): AboutLine {
    const members = usePodMembers(podId);
    const requests = useJoinRequests(podId);
    const asking = requests.isSuccess ? requests.data.length : 0;
    return peopleLine(members.isSuccess ? members.data.length : null, asking);
}
