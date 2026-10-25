import type { LiveContext } from "../../context";
import { pendingFor, personOf } from "./model";
import type { PeopleSource } from "./source";
import { joinWire, readJoin, readJoinRequest, type JoinRequest } from "@web/data/joining";

function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    return (value as { items?: unknown[] } | null)?.items ?? [];
}

/** The same calls the web makes (`data/live.ts` and `shell/add-people.tsx`). */
export function peopleLive(context: LiveContext): PeopleSource {
    const pod = (podId: string) => context.client(podId);
    return {
        async members(podId) {
            return itemsOf(await pod(podId).podMembers.list(podId)).map(personOf);
        },
        async invitations(orgId, podId) {
            return pendingFor(itemsOf(await context.client().organizations.invitations.list(orgId, { limit: 50 })), podId);
        },
        async add(podId, orgMemberId, role) {
            await pod(podId).podMembers.add(podId, { organization_member_id: orgMemberId, roles: [role] });
        },
        async invite(orgId, podId, email, role) {
            const made = (await context.client().organizations.invitations.invite(orgId, {
                email,
                /* The least the organization can give. The access here is
                   what they were asked for. */
                role: "ORG_MEMBER" as never,
                pod_id: podId,
                pod_role: role,
                /* Relative, so it resolves against whichever address they
                   open the invitation on (the web's own). */
                redirect_uri: "/t/" + encodeURIComponent(podId),
            })) as { email?: string | null; accept_url?: string | null; emailed?: boolean | null };
            return { email: made.email ?? email, acceptUrl: made.accept_url ?? null, emailed: made.emailed ?? null };
        },
        async joinPolicy(podId) {
            const row = (await pod(podId).pods.get(podId)) as { config?: { join_policy?: unknown } | null };
            return readJoin(row.config?.join_policy);
        },
        async setJoinPolicy(podId, policy) {
            /* Only this field goes up: the server merges the config field-wise,
               so the runtime stored beside it survives. */
            await pod(podId).pods.update(podId, { config: { join_policy: joinWire(policy) as never } });
        },
        async joinRequests(podId) {
            const listed = await pod(podId).podJoinRequests.list(podId, { status: "PENDING" as never, limit: 50 });
            return itemsOf(listed).map(readJoinRequest).filter((one): one is JoinRequest => one !== null);
        },
        async admit(podId, requestId) {
            const said = readJoinRequest(await pod(podId).podJoinRequests.approve(podId, requestId));
            if (!said) throw new Error("They may be in; the answer could not be read. Open People again to check.");
            return said;
        },
    };
}
