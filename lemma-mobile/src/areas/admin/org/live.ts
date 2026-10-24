import type { LemmaClient } from "lemma-sdk";
import type { LiveContext } from "../../context";
import { readInvitation } from "./model";
import type { Invitation, OrgSource } from "./source";
import { orgJoinWire, readOrgJoin } from "@web/data/joining";

/** The role as the SDK types it: a generated enum it does not export, read
 *  off the method that takes it. The three values are spelled once, in
 *  `web/membership.ts`. */
type RoleWire = Parameters<LemmaClient["organizations"]["members"]["updateRole"]>[2];

function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    const items = (value as { items?: unknown } | null)?.items;
    return Array.isArray(items) ? items : [];
}

/** The same calls the web makes (`org/people.tsx`; `data/live.ts`
 *  `getOrgJoin`, `setOrgJoin`). */
export function orgLive(context: LiveContext): OrgSource {
    const org = () => context.client().organizations;
    return {
        async invitations(orgId) {
            const listed = await org().invitations.list(orgId, { limit: 100 });
            return itemsOf(listed).map(readInvitation).filter((one): one is Invitation => one !== null);
        },
        async invite(orgId, email, role) {
            const made = await org().invitations.invite(orgId, { email: email.trim(), role: role as unknown as RoleWire });
            /* The answer says whether anybody was emailed, so one this app
               cannot read is still an invitation, said as one that was. */
            return readInvitation(made) ?? { id: "", email: email.trim(), role, status: "PENDING", expires_at: null, accept_url: null, emailed: null };
        },
        async revoke(invitationId) {
            await org().invitations.revoke(invitationId);
        },
        async setRole(orgId, memberId, role) {
            await org().members.updateRole(orgId, memberId, role as unknown as RoleWire);
        },
        async remove(orgId, memberId) {
            await org().members.remove(orgId, memberId);
        },
        async join(orgId) {
            const row = (await org().get(orgId)) as { join_policy?: unknown; email_domain?: unknown };
            return readOrgJoin(row.join_policy, row.email_domain);
        },
        async setJoin(orgId, join) {
            /* Through the raw request, as the web does: the SDK does not put
               the organization update on `organizations`. The domain goes up
               only with the rule that uses it, so closing the door for a week
               does not throw away what somebody typed. */
            await context.client().request("PATCH", `/organizations/${encodeURIComponent(orgId)}`, {
                body: join.policy === "domain"
                    ? { join_policy: orgJoinWire(join.policy), email_domain: join.domain }
                    : { join_policy: orgJoinWire(join.policy) },
            });
        },
    };
}
