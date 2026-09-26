/** Who may do what to an organization's membership.
 *
 *  The server decides all of this and will refuse anything it disagrees with.
 *  These are here so the screen does not offer an action it already knows will
 *  be refused — an "Invite" button that always errors is worse than no button.
 */

export type Role = "ORG_OWNER" | "ORG_EDITOR" | "ORG_MEMBER";

export const ROLES: { value: Role; label: string; blurb: string }[] = [
    { value: "ORG_OWNER", label: "Owner", blurb: "Everything, including billing and removing people." },
    { value: "ORG_EDITOR", label: "Editor", blurb: "Build and change teammates, connectors and keys." },
    { value: "ORG_MEMBER", label: "Member", blurb: "Work with the teammates that already exist." },
];

export function roleLabel(role: string | null | undefined): string {
    const known = ROLES.find((entry) => entry.value === role);
    if (known) return known.label;
    /* An unknown role still has to read as something. The wire format is the
       fallback, tidied — a blank cell looks like a bug in the list. */
    return String(role ?? "").replace(/^ORG_/, "").toLowerCase() || "—";
}

/** Whether somebody with this role can change the membership at all. */
export function canManage(role: string | null | undefined): boolean {
    return role === "ORG_OWNER" || role === "ORG_EDITOR";
}

/** Only an owner may hand out or take away ownership.
 *
 *  An editor can invite and can remove a member, but promoting somebody to
 *  owner is how an editor would give themselves ownership by proxy.
 */
export function canSetRole(myRole: string | null | undefined, target: Role): boolean {
    if (target === "ORG_OWNER") return myRole === "ORG_OWNER";
    return canManage(myRole);
}

export interface Member {
    id: string;
    role?: string | null;
    user_id?: string | null;
    user?: { email?: string | null; first_name?: string | null; last_name?: string | null } | null;
    email?: string | null;
}

/** The last owner cannot be removed or demoted, because an organization with
 *  no owner is one nobody can ever change again. The server enforces it; this
 *  is so the button is not offered in the first place. */
export function isLastOwner(members: Member[], memberId: string): boolean {
    const owners = members.filter((member) => member.role === "ORG_OWNER");
    return owners.length === 1 && owners[0].id === memberId;
}

/** What to call a member, given the server may know only their address. */
export function memberName(member: Member): string {
    const full = [member.user?.first_name, member.user?.last_name].filter(Boolean).join(" ").trim();
    if (full) return full;
    return member.user?.email ?? member.email ?? "Member";
}

/** Their address, where it is known and is not already the name. */
export function memberEmail(member: Member): string {
    const email = member.user?.email ?? member.email ?? "";
    return email === memberName(member) ? "" : email;
}

/** Deliberately loose. An address is valid if the server accepts it, and a
 *  regex that thinks it knows better is how a legitimate address gets refused
 *  by a form that never asked anybody. This only catches what cannot work. */
export function inviteProblem(email: string): string | null {
    const trimmed = email.trim();
    if (!trimmed) return "An email address is needed.";
    if (/\s/.test(trimmed)) return "An address cannot contain spaces.";
    const at = trimmed.indexOf("@");
    if (at <= 0 || at !== trimmed.lastIndexOf("@")) return "That does not look like an email address.";
    if (at === trimmed.length - 1) return "That address is missing everything after the @.";
    return null;
}

/** Somebody already here, or already asked, should not be asked again. */
export function alreadyKnown(
    email: string,
    members: Member[],
    pending: { email?: string | null; status?: string | null }[],
): string | null {
    const wanted = email.trim().toLowerCase();
    if (!wanted) return null;
    if (members.some((member) => (member.user?.email ?? member.email ?? "").toLowerCase() === wanted)) {
        return "They are already in this organization.";
    }
    if (pending.some((invite) => (invite.email ?? "").toLowerCase() === wanted && invite.status === "PENDING")) {
        return "They have already been invited.";
    }
    return null;
}

/** Only an owner may move the organization's door.
 *
 *  Stricter than `canManage` on purpose, and the platform agrees — the update
 *  route is owner-only. An editor invites people one at a time; opening the
 *  organization to a whole email domain, or to everybody, is a decision about
 *  what the organization *is*. Everyone else still reads the rule: a setting
 *  you cannot see is one you cannot ask anybody to change.
 */
export function canSetJoinPolicy(role: string | null | undefined): boolean {
    return role === "ORG_OWNER";
}

/** An invitation as its organization's owners and editors are sent it. */
export interface InvitationForInviter {
    email?: string | null;
    accept_url?: string | null;
    emailed?: boolean | null;
}

/** What to tell the inviter when nobody was emailed, or null when somebody was.
 *
 *  The server says `emailed: false` where email is not set up -- Lemma Desktop
 *  until somebody configures it -- and the invitation then exists with nobody
 *  told about it. Saying "Invited" there is how an invitation sits unanswered
 *  for a week. Only `false` counts: an older server that never said is not
 *  known to have failed. */
export function unsentInvitation(invite: InvitationForInviter): { to: string; link: string | null; said: string } | null {
    if (invite.emailed !== false) return null;
    const to = invite.email?.trim() || "them";
    return {
        to,
        link: invite.accept_url?.trim() || null,
        said: "Email isn't set up on this server, so the invitation to " + to +
            " wasn't emailed. Share this link with them instead.",
    };
}
