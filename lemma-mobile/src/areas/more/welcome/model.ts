import { routes } from "../../../data/routes";
import { POD_ROLES } from "@web/data/pod-roles";
import { roleLabel } from "@web/org/membership";
import type { Invitation, InvitationDetail, InvitationStatus, SuggestedOrg } from "./source";

/** The phone's own reading of the first morning, on top of the web's
 *  `web/arrival.ts` and `web/first-profile.ts`: the server's shapes, what an
 *  invitation says, where arriving lands, and the words. Pure, so `tests/`
 *  can hold it. */

type Raw = Record<string, unknown>;

const record = (value: unknown): Raw => (value && typeof value === "object" && !Array.isArray(value) ? (value as Raw) : {});
const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

function itemsOf(value: unknown): Raw[] {
    if (Array.isArray(value)) return value.map(record);
    const items = record(value).items;
    return Array.isArray(items) ? items.map(record) : [];
}

/** An organization's name as the switcher says it (the web's `shortOrgName`
 *  in `data/live.ts`): "Ada's Personal" is "Ada · personal". */
export function orgName(name: string): string {
    const personal = /^(.+?)'s Personal$/.exec(name);
    if (!personal) return name;
    return personal[1].replace(/@.*$/, "") + " · personal";
}

/** Organizations off the wire: the ones with an id and a name. */
export function readOrgs(listed: unknown): SuggestedOrg[] {
    return itemsOf(listed).flatMap((org) => {
        const id = text(org.id);
        const name = text(org.name);
        return id && name ? [{ id, name: orgName(name) }] : [];
    });
}

/** Invitations off the wire (the web's `myInvitations`). One without an id
 *  or an organization cannot be accepted, so it is not offered. */
export function readInvitations(listed: unknown): Invitation[] {
    return itemsOf(listed).flatMap((invite): Invitation[] => {
        const id = text(invite.id);
        const orgId = text(invite.organization_id);
        if (!id || !orgId) return [];
        return [{
            id,
            orgId,
            /* An organization that sent no name is still one somebody can
               accept, and "an organization" is truer than a blank. */
            orgName: orgName(text(invite.organization_name)) || "an organization",
            podId: text(invite.pod_id) || null,
            podName: text(invite.pod_name) || null,
            podAbout: text(invite.pod_description) || null,
            role: text(invite.role) || "ORG_MEMBER",
        }];
    });
}

const STATUSES: readonly InvitationStatus[] = ["PENDING", "ACCEPTED", "EXPIRED", "REVOKED"];

/** One invitation read from its link. An unknown status reads as lapsed,
 *  never as open: offering Accept on something the server would refuse is
 *  the worse of the two mistakes. */
export function readInvitationDetail(raw: unknown): InvitationDetail {
    const said = record(raw);
    const status = text(said.status).toUpperCase();
    return {
        id: text(said.id),
        orgId: text(said.organization_id),
        orgName: orgName(text(said.organization_name)) || "An organization",
        email: text(said.email),
        role: text(said.role) || "ORG_MEMBER",
        podRole: text(said.pod_role) || null,
        podId: text(said.pod_id) || null,
        podName: text(said.pod_name) || null,
        podAbout: text(said.pod_description) || null,
        status: (STATUSES as readonly string[]).includes(status) ? (status as InvitationStatus) : "EXPIRED",
        expiresAt: text(said.expires_at) || null,
        redirectUri: text(said.redirect_uri) || null,
    };
}

/** The organization role as a word: "member", "editor", "owner". */
export function roleWord(role: string | null | undefined): string {
    const said = roleLabel(role);
    return said === "—" ? "member" : said.toLowerCase();
}

/** A role in a Lemma as a word: "editor", "viewer". */
export function podRoleWord(role: string | null | undefined): string | null {
    if (!role) return null;
    const known = POD_ROLES.find((one) => one.value === role);
    return (known ? known.role : role.replace(/^POD_/, "")).toLowerCase();
}

/** What an invitation is, on its row: the Lemma it brings you in to work
 *  with when it names one, else the role. */
export function inviteLine(invite: Pick<Invitation, "podName" | "podAbout" | "role">): string {
    if (!invite.podName) return `Invited as ${article(roleWord(invite.role))} ${roleWord(invite.role)}`;
    const about = invite.podAbout?.trim();
    return about ? `Invited to work with ${invite.podName}. ${about}` : `Invited to work with ${invite.podName}`;
}

function article(word: string): string {
    return /^[aeiou]/i.test(word) ? "an" : "a";
}

/** The Lemma an invitation's `redirect_uri` lands on: `/t/{podId}`, on the
 *  web's address or as a path. Null for anything else, which lands on Home:
 *  only the web's own workspace address names a Lemma. */
export function podFromRedirect(redirectUri: string | null | undefined): string | null {
    const raw = (redirectUri ?? "").trim();
    if (!raw) return null;
    let path = raw;
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        const after = raw.replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]*/i, "");
        path = after || "/";
    }
    const match = /^\/t\/([^/?#]+)/.exec(path);
    if (!match) return null;
    try {
        return decodeURIComponent(match[1]) || null;
    } catch {
        return null;
    }
}

/** Where arriving lands: the Lemma somebody was brought in for, else Home. */
export function landingRoute(podId: string | null | undefined): string {
    return podId ? routes.lemma(podId) : routes.home;
}

/** "23 October 2026", in the phone's own day; "" for nothing readable. */
export function spokenDate(iso: string | null | undefined, now = new Date()): string {
    if (!iso) return "";
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    return `${at.getDate()} ${months[at.getMonth()]}${at.getFullYear() === now.getFullYear() ? "" : ` ${at.getFullYear()}`}`;
}

/** What an invitation read from its link says about where it stands, and
 *  whether it can still be acted on. A pending one past its time is said
 *  as lapsed, which is what the server will answer. */
export function invitationStanding(invite: Pick<InvitationDetail, "status" | "expiresAt">, now = Date.now()): { open: boolean; said: string | null } {
    const lapses = invite.expiresAt ? Date.parse(invite.expiresAt) : Number.NaN;
    const lapsedOn = spokenDate(invite.expiresAt, new Date(now));
    if (invite.status === "PENDING" && !(Number.isFinite(lapses) && lapses < now)) return { open: true, said: null };
    if (invite.status === "ACCEPTED") return { open: false, said: "This invitation has been accepted already." };
    if (invite.status === "REVOKED") return { open: false, said: "This invitation was declined or withdrawn, so it can’t be accepted now." };
    return {
        open: false,
        said: lapsedOn ? `This invitation lapsed on ${lapsedOn}. Ask whoever sent it for a new one.` : "This invitation has lapsed. Ask whoever sent it for a new one.",
    };
}

/** When a link could not be read: not open to this account, lapsed, or gone. */
export const UNREADABLE_INVITATION = "This invitation isn’t open to the account you’re signed in with. It may have lapsed or been withdrawn.";

/** The heading for somebody who already belongs somewhere and has nothing
 *  waiting: the web only ever shows this screen to somebody with no
 *  organization, so its question ("Who will you be working with?") would
 *  be asked of somebody who has answered it. */
export function memberHeading(invitations: number, matches: number, fallback: string): string {
    return invitations > 0 || matches > 0 ? fallback : "Nothing waiting for you";
}

export const MEMBER_LEDE = "Invitations to other organizations, and organizations open to your email address, show here.";

/** The lede under the arrival heading, said the phone's way. */
export function arrivalLede(invitations: number, matches: number): string {
    if (invitations > 0) return "Accept an invitation to join your team.";
    if (matches > 0) return "You can join with your verified work email.";
    return "This decides who else can see your Lemmas and the work they do.";
}

/** The two kinds of organization, as the cards say them. */
export const KINDS = {
    personal: { title: "Just me", line: "Yours alone. You can bring people in later." },
    team: { title: "My team", line: "Start Lemmas for your team and choose who can open each one." },
} as const;

/** Organizations already held, so the name read off an email domain is not
 *  offered twice. Compared the way `freshTeamNameFor` compares. */
export function takenNames(orgs: readonly { name: string }[]): string[] {
    return orgs.map((one) => one.name);
}

/** Sample data keeps its one organization whatever is accepted, joined or
 *  made: said plainly, instead of pretending the switch happened. */
export function sampleArrival(entered: string, kept: string): string {
    return `Sample data keeps one organization, ${kept}, so nothing was added here. With your own account you would now be in ${entered}.`;
}
