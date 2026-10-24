import type { Invitation, InvitationDetail, SuggestedOrg, WelcomeSource } from "./source";

/** The first morning as sample data: two invitations waiting for Ada (one
 *  to work with Launch, one from Globex as an editor), one organization her
 *  email domain may join, and making one, all in memory. The organization
 *  list itself is fixed (`data/sample.ts`: Acme and nothing else), so
 *  accepting, joining or making one changes nothing there, and the screens
 *  say so rather than pretend.
 *
 *  Invitation links for each state, `lemma://invitations/<id>/accept`:
 *  `sample-invite-launch` and `sample-invite-globex` (open),
 *  `sample-invite-accepted`, `sample-invite-withdrawn`, `sample-invite-lapsed`,
 *  and any other id, which cannot be read. Declining is refused the way the
 *  server refuses it today: only the organization's own people may revoke
 *  an invitation. */

const DAY = 24 * 60 * 60_000;
const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function failure(message: string, statusCode: number): Error {
    return Object.assign(new Error(message), { statusCode });
}

interface Held extends InvitationDetail {
    /** Listed among Ada's pending invitations on the first morning. */
    listed: boolean;
}

function invitations(now: number): Held[] {
    const base = { email: "ada@acme.example", podRole: null, podId: null, podName: null, podAbout: null, redirectUri: null, listed: false };
    return [
        {
            ...base, id: "sample-invite-launch", orgId: "sample-acme", orgName: "Acme", role: "ORG_MEMBER", status: "PENDING",
            podRole: "POD_EDITOR", podId: "sample-launch-1440", podName: "Launch", podAbout: "Runs the October launch.",
            expiresAt: new Date(now + 6 * DAY).toISOString(), redirectUri: "/t/sample-launch-1440", listed: true,
        },
        { ...base, id: "sample-invite-globex", orgId: "sample-globex", orgName: "Globex", role: "ORG_EDITOR", status: "PENDING", expiresAt: new Date(now + 12 * DAY).toISOString(), listed: true },
        { ...base, id: "sample-invite-accepted", orgId: "sample-initech", orgName: "Initech", role: "ORG_MEMBER", status: "ACCEPTED", expiresAt: new Date(now + 3 * DAY).toISOString() },
        { ...base, id: "sample-invite-withdrawn", orgId: "sample-umbrella", orgName: "Umbrella", role: "ORG_MEMBER", status: "REVOKED", expiresAt: new Date(now + 2 * DAY).toISOString() },
        { ...base, id: "sample-invite-lapsed", orgId: "sample-hooli", orgName: "Hooli", role: "ORG_OWNER", status: "EXPIRED", expiresAt: new Date(now - 9 * DAY).toISOString() },
    ];
}

const SUGGESTED: SuggestedOrg[] = [{ id: "sample-acme-labs", name: "Acme Labs" }];

export function welcomeSample(): WelcomeSource {
    const held = invitations(Date.now());
    const joined = new Set<string>();
    let made = 0;

    const find = (id: string): Held => {
        const one = held.find((invite) => invite.id === id);
        if (!one) throw failure("Invitation not found", 404);
        return one;
    };
    const detail = ({ listed: _listed, ...invite }: Held): InvitationDetail => ({ ...invite });

    return {
        async myInvitations() {
            await wait(350);
            return held
                .filter((invite) => invite.listed && invite.status === "PENDING")
                .map((invite): Invitation => ({ id: invite.id, orgId: invite.orgId, orgName: invite.orgName, podId: invite.podId, podName: invite.podName, podAbout: invite.podAbout, role: invite.role }));
        },
        async suggestedOrgs() {
            await wait(300);
            return SUGGESTED.filter((org) => !joined.has(org.id));
        },
        async acceptInvitation(invitationId) {
            await wait(700);
            const invite = find(invitationId);
            if (invite.status !== "PENDING") throw failure(`Invitation is not pending (status: ${invite.status})`, 400);
            invite.status = "ACCEPTED";
            return { redirectUri: invite.redirectUri };
        },
        async joinOrg(orgId) {
            await wait(600);
            joined.add(orgId);
        },
        async createOrg(wanted) {
            await wait(800);
            made += 1;
            return { id: `sample-made-org-${made}`, name: wanted.name };
        },
        async invitation(invitationId) {
            await wait(300);
            return detail(find(invitationId));
        },
        async declineInvitation(invitationId) {
            await wait(500);
            find(invitationId);
            throw failure("You may not revoke this organization's invitations", 403);
        },
    };
}
