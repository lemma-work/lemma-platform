/** The first morning (PARITY.md, G; the web's `org/arrival-view.tsx`,
 *  `org/new-org-dialog.tsx` and `site/invitation.tsx`): invitations waiting
 *  for the signed-in person, organizations their email domain may join,
 *  making one, and an invitation opened from its link.
 *
 *  The same calls the web makes (`data/live.ts`: `myInvitations`,
 *  `acceptInvitation`, `suggestedOrgs`, `joinOrg`, `createOrg`). The live
 *  side is `live.ts`, the sample side `sample.ts`; screens reach it through
 *  `hooks.ts`, never the SDK directly. The first profile step reads and
 *  writes the profile through Settings' own account part. */

/** An invitation addressed to whoever is signed in. */
export interface Invitation {
    id: string;
    orgId: string;
    /** "an organization" when the server sent no name. */
    orgName: string;
    /** The Lemma it brings you in to work with, when it names one. */
    podId: string | null;
    podName: string | null;
    podAbout: string | null;
    /** `ORG_MEMBER`, `ORG_EDITOR`, `ORG_OWNER`. */
    role: string;
}

/** An organization this person's email domain may walk into. */
export interface SuggestedOrg {
    id: string;
    name: string;
}

/** What "Create" sends. `emailDomain` and the open policy go together or
 *  not at all; `derived` when the name is the one the app made up. */
export interface NewOrg {
    name: string;
    emailDomain?: string;
    derived?: boolean;
}

export type InvitationStatus = "PENDING" | "ACCEPTED" | "EXPIRED" | "REVOKED";

/** One invitation, read from its link. */
export interface InvitationDetail {
    id: string;
    orgId: string;
    orgName: string;
    /** The account it was sent to. */
    email: string;
    role: string;
    /** The role in the Lemma it names, when it names one. */
    podRole: string | null;
    podId: string | null;
    podName: string | null;
    podAbout: string | null;
    status: InvitationStatus;
    /** ISO; null when the server sent none it could read. */
    expiresAt: string | null;
    /** Where the invitation means to land you: `/t/{podId}` for a Lemma. */
    redirectUri: string | null;
}

export interface WelcomeSource {
    /** Pending invitations to the signed-in person. */
    myInvitations(): Promise<Invitation[]>;
    /** Empty is the ordinary answer, and so is a failure: an optional
     *  suggestion that could not be fetched offers nothing rather than
     *  turning somebody's first screen into an error (the web's rule). */
    suggestedOrgs(): Promise<SuggestedOrg[]>;
    /** Answers where the invitation means to land you. */
    acceptInvitation(invitationId: string): Promise<{ redirectUri: string | null }>;
    joinOrg(orgId: string): Promise<void>;
    createOrg(wanted: NewOrg): Promise<SuggestedOrg>;
    invitation(invitationId: string): Promise<InvitationDetail>;
    /** As the web declines one: the invitation is revoked. */
    declineInvitation(invitationId: string): Promise<void>;
}
