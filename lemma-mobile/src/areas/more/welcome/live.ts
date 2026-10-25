import type { LiveContext } from "../../context";
import { orgName, readInvitationDetail, readInvitations, readOrgs } from "./model";
import type { WelcomeSource } from "./source";

/** The same calls the web makes (`data/live.ts`: `myInvitations`,
 *  `acceptInvitation`, `suggestedOrgs`, `joinOrg`, `createOrg`;
 *  `site/invitation.tsx`: `invitations.get`, `accept`, `revoke`), read by
 *  `model.ts`. The two the SDK has no method for go through `request`. */
export function welcomeLive(context: LiveContext): WelcomeSource {
    const client = () => context.client();
    return {
        async myInvitations() {
            /* `PENDING` said out loud, as the web says it: a default that
               moved would otherwise start offering expired ones. */
            return readInvitations(await client().organizations.invitations.listMine({ status: "PENDING" as never, limit: 50 }));
        },

        async suggestedOrgs() {
            try {
                return readOrgs(await client().request("GET", "/organizations/suggested", { params: { limit: 20 } }));
            } catch {
                return [];
            }
        },

        async acceptInvitation(invitationId) {
            const said = (await client().organizations.invitations.accept(invitationId)) as { redirect_uri?: string | null } | undefined;
            return { redirectUri: said?.redirect_uri?.trim() || null };
        },

        async joinOrg(orgId) {
            await client().request("POST", `/organizations/${encodeURIComponent(orgId)}/join`);
        },

        async createOrg(wanted) {
            const made = (await client().organizations.create({
                name: wanted.name,
                /* A name nobody typed may be made unique; one they typed is
                   refused when taken, so they hear about it. */
                resolve_name_conflicts: wanted.derived === true,
                ...(wanted.emailDomain ? { email_domain: wanted.emailDomain, join_policy: "EMAIL_DOMAIN" as never } : {}),
            })) as { id?: string; name?: string };
            if (!made?.id) throw new Error("The organization was made but came back without an id.");
            return { id: made.id, name: orgName(made.name ?? wanted.name) };
        },

        async invitation(invitationId) {
            return readInvitationDetail(await client().organizations.invitations.get(invitationId));
        },

        async declineInvitation(invitationId) {
            await client().organizations.invitations.revoke(invitationId);
        },
    };
}
