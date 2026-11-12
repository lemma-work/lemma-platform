import type { Member } from "@web/org/membership";

/** Who may do what, asked of the server rather than guessed from a role
 *  (the web's `podPermissions.me` and its members list). Every part reads
 *  these through `access/hooks.ts`, so a screen offers only what the server
 *  will allow, and asks once between them. */
export interface AccessSource {
    /** The actions the signed-in person holds in a Lemma: `pod.member.manage`,
     *  `pod.delete`, … (`web/pod-roles.ts`). */
    podActions(podId: string): Promise<string[]>;
    /** The organization's members, as the server lists them: the only thing
     *  that says what the signed-in person is there. */
    orgMembers(orgId: string): Promise<Member[]>;
    /** The deployment the server is: "local" for an installation on somebody's
     *  own computer (no billing), else whatever the web calls it, or null. */
    deployment(): Promise<string | null>;
}
