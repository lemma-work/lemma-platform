import type { PendingInvite, PodPerson, SentInvite } from "./model";
import type { JoinPolicy, JoinRequest } from "@web/data/joining";

/** Who has access to a Lemma: add people, who can join, join requests (PARITY.md, F).
 *
 *  The web's roster, Add people, "Who else can join" and the queue at the
 *  door (`shell/add-people.tsx`, `who-can-join.tsx`, `at-the-door.tsx`), with
 *  the same calls (`data/live.ts`: `membersOf`, `getPodJoin`, `setPodJoin`,
 *  `listJoinRequests`, `admitToPod`). The live side is `live.ts`, the sample
 *  side `sample.ts`; screens reach it through `hooks.ts`. */
export interface PeopleSource {
    /** Everyone with access to the Lemma, and what each may do there. */
    members(podId: string): Promise<PodPerson[]>;
    /** The organization's invitations still waiting that name this Lemma, so
     *  nobody invites the same address twice wondering if the first went. */
    invitations(orgId: string, podId: string): Promise<PendingInvite[]>;
    /** Give somebody already in the organization access, at this level
     *  (`POD_EDITOR`, …), by their organization membership's id. */
    add(podId: string, orgMemberId: string, role: string): Promise<void>;
    /** Invite somebody new: an organization invitation that names this Lemma
     *  and the access it gives, so accepting joins both and lands them here. */
    invite(orgId: string, podId: string, email: string, role: string): Promise<SentInvite>;
    /** Who may let themselves in. */
    joinPolicy(podId: string): Promise<JoinPolicy>;
    setJoinPolicy(podId: string, policy: JoinPolicy): Promise<void>;
    /** The asks still waiting. Listing them is an admin's call, so most
     *  people are refused, which is the ordinary case. */
    joinRequests(podId: string): Promise<JoinRequest[]>;
    /** Let one in. The server picks what they may do; the roster changes it. */
    admit(podId: string, requestId: string): Promise<JoinRequest>;
}
