import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey, useMe } from "../../../data/hooks";
import { useOrgMembers } from "../access/hooks";
import { adminKey, useAdminSource } from "../hooks";
import { canManage, type Member, type Role } from "@web/org/membership";
import { pendingOf } from "./model";
import type { Invitation } from "./source";
import type { OrgJoin } from "@web/data/joining";

/** This part's reads and writes, through react-query. Key every query with
 *  `adminKey(useCacheKey(), "org", …)`; the members themselves are the
 *  access part's one read (`useOrgMembers`). */
export function useOrgSource() {
    return useAdminSource().org;
}

const membersKey = (key: string, orgId: string | null | undefined) => adminKey(key, "access", "org-members", orgId);
const invitationsKey = (key: string, orgId: string | null | undefined) => adminKey(key, "org", "invitations", orgId);
const joinKey = (key: string, orgId: string | null | undefined) => adminKey(key, "org", "join", orgId);

export function useInvitations(orgId: string | null | undefined, enabled = true) {
    const source = useOrgSource();
    const key = useCacheKey();
    return useQuery({ queryKey: invitationsKey(key, orgId), queryFn: () => source.invitations(orgId!), enabled: Boolean(orgId) && enabled, staleTime: 30_000 });
}

export function useOrgJoin(orgId: string | null | undefined) {
    const source = useOrgSource();
    const key = useCacheKey();
    return useQuery({ queryKey: joinKey(key, orgId), queryFn: () => source.join(orgId!), enabled: Boolean(orgId), staleTime: 60_000 });
}

/** Everything the People screens decide from, read once: the members, the
 *  signed-in person's role among them (the only thing that says what they
 *  are here), whether they manage the membership, and the invitations still
 *  waiting. Invitations are asked for only by somebody who can manage them;
 *  for anybody else the server would refuse. */
export function useOrgPeople(orgId: string | null | undefined) {
    const me = useMe();
    const members = useOrgMembers(orgId);
    const people = useMemo(() => members.data ?? [], [members.data]);
    const myId = me.data?.id ?? null;
    const myRole = people.find((member) => member.user_id && member.user_id === myId)?.role ?? null;
    const manage = canManage(myRole);
    const invitations = useInvitations(orgId, manage);
    const pending = useMemo(() => pendingOf(invitations.data ?? []), [invitations.data]);
    return { members, people, myId, myRole, manage, invitations, pending, answered: members.isSuccess && me.isSuccess };
}

/** The members and the invitations, read again after a change. */
function useRefresh(orgId: string) {
    const cache = useQueryClient();
    const key = useCacheKey();
    return () => {
        void cache.invalidateQueries({ queryKey: membersKey(key, orgId) });
        void cache.invalidateQueries({ queryKey: invitationsKey(key, orgId) });
    };
}

/** Change the members every part reads the moment a write lands, then read
 *  them again: a fresh list, so every screen that holds it draws anew. */
function usePatchMembers(orgId: string) {
    const cache = useQueryClient();
    const key = useCacheKey();
    return (change: (held: Member[]) => Member[]) => {
        cache.setQueryData<Member[]>(membersKey(key, orgId), (held) => (held ? change(held) : held));
        void cache.invalidateQueries({ queryKey: membersKey(key, orgId) });
    };
}

export function useInvite(orgId: string) {
    const source = useOrgSource();
    const refresh = useRefresh(orgId);
    return useMutation({
        mutationFn: ({ email, role }: { email: string; role: Role }) => source.invite(orgId, email, role),
        onSuccess: refresh,
    });
}

export function useRevoke(orgId: string) {
    const source = useOrgSource();
    const cache = useQueryClient();
    const key = useCacheKey();
    const refresh = useRefresh(orgId);
    return useMutation({
        mutationFn: (invitationId: string) => source.revoke(invitationId),
        onSuccess: (_answer, invitationId) => {
            cache.setQueryData<Invitation[]>(invitationsKey(key, orgId), (held) => held?.filter((one) => one.id !== invitationId));
            refresh();
        },
    });
}

export function useSetRole(orgId: string) {
    const source = useOrgSource();
    const patch = usePatchMembers(orgId);
    return useMutation({
        mutationFn: ({ memberId, role }: { memberId: string; role: Role }) => source.setRole(orgId, memberId, role),
        onSuccess: (_answer, { memberId, role }) => patch((held) => held.map((one) => (one.id === memberId ? { ...one, role } : one))),
    });
}

export function useRemoveMember(orgId: string) {
    const source = useOrgSource();
    const patch = usePatchMembers(orgId);
    return useMutation({
        mutationFn: (memberId: string) => source.remove(orgId, memberId),
        onSuccess: (_answer, memberId) => patch((held) => held.filter((one) => one.id !== memberId)),
    });
}

/** Change who may let themselves in: shown at once, then read again. */
export function useSetOrgJoin(orgId: string) {
    const source = useOrgSource();
    const cache = useQueryClient();
    const key = useCacheKey();
    return useMutation({
        mutationFn: (join: OrgJoin) => source.setJoin(orgId, join),
        onSuccess: (_answer, join) => {
            cache.setQueryData(joinKey(key, orgId), join);
            void cache.invalidateQueries({ queryKey: joinKey(key, orgId) });
        },
    });
}
