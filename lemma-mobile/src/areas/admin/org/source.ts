import type { Role } from "@web/org/membership";
import type { OrgJoin } from "@web/data/joining";

/** An invitation to the organization, as its owners and editors are sent it
 *  (the wire's own field names, so the web's rules in `web/membership.ts`,
 *  `unsentInvitation` and `alreadyKnown`, read it as it is). */
export interface Invitation {
    id: string;
    email: string | null;
    role: string | null;
    /** `PENDING` until it is accepted, revoked or runs out. */
    status: string | null;
    expires_at: string | null;
    /** The link the invited person opens, for an owner or editor to hand over
     *  themselves when nobody was emailed. */
    accept_url: string | null;
    /** False where this server does not send email: nobody was told. */
    emailed: boolean | null;
}

/** The organization's people: members, invitations, roles, who can join
 *  (PARITY.md, F; the web's `org/people.tsx` and `org/who-can-join-org.tsx`).
 *
 *  The members themselves are read once for every part, through
 *  `access/hooks.ts` `useOrgMembers`; this part changes them. The server
 *  decides who may do what and refuses the rest; the screens offer only what
 *  `web/membership.ts` says it will allow. */
export interface OrgSource {
    /** Every invitation the organization has sent; the screens keep the pending ones. */
    invitations(orgId: string): Promise<Invitation[]>;
    /** Ask somebody in by email, with the role they will have. Answers the
     *  invitation made, which says whether anybody was emailed. */
    invite(orgId: string, email: string, role: Role): Promise<Invitation>;
    /** Withdraw an invitation nobody has accepted yet. */
    revoke(invitationId: string): Promise<void>;
    /** Give a member another role. */
    setRole(orgId: string, memberId: string, role: Role): Promise<void>;
    /** Take somebody out of the organization. */
    remove(orgId: string, memberId: string): Promise<void>;
    /** Who may let themselves in: only the invited, anyone at an email
     *  domain, or anyone with a Lemma account. */
    join(orgId: string): Promise<OrgJoin>;
    /** Change who may let themselves in (owners only). The domain goes up only
     *  with the rule that uses it, so it is kept when the rule moves away. */
    setJoin(orgId: string, join: OrgJoin): Promise<void>;
}
