"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { lemma } from "@/session/client";

export interface OrgPerson {
    /** The organization membership id, which is what adding to a Lemma takes. */
    id: string;
    label: string;
}

interface MemberRow {
    id: string;
    role?: string | null;
    user?: { email?: string | null; name?: string | null } | null;
}

/** The people a new Lemma can be given: everyone in the organization but the
 *  reader, who is in it by making it, and its owners, who see every Lemma
 *  already. */
export function useOrgPeople(orgId: string) {
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current() as Promise<{ email?: string } | undefined>,
        enabled: source.label === "live",
        staleTime: 5 * 60_000,
    });
    const members = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: () => lemma().organizations.members.list(orgId, { limit: 100 }),
        enabled: source.label === "live",
        staleTime: 60_000,
    });
    return useMemo<OrgPerson[]>(() => {
        const mine = (me.data?.email ?? "").toLowerCase();
        const rows = (((members.data as { items?: unknown[] } | undefined)?.items) ?? []) as MemberRow[];
        return rows
            .filter((row) => row.role !== "ORG_OWNER")
            .map((row) => ({ id: row.id, label: row.user?.name || row.user?.email || "Member", email: (row.user?.email ?? "").toLowerCase() }))
            .filter((person) => !mine || person.email !== mine)
            .map(({ id, label }) => ({ id, label }));
    }, [me.data, members.data]);
}
