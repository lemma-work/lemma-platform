/** What the organization's People screens decide, away from the screens:
 *  what to call a row, what a role is for, what the signed-in person may do
 *  to whom, and what a typed address or domain is. Pure, so `tests/` can
 *  hold it. The rules themselves are the web's (`web/membership.ts`,
 *  `org/web/joining.ts`); this says them for a phone. */

import {
    ROLES, alreadyKnown, canActOn, canManage, canSetRole, inviteProblem, isLastOwner,
    memberEmail, memberName, type Member, type Role,
} from "@web/org/membership";
import type { OrgJoin, OrgJoinPolicy } from "@web/data/joining";
import type { Invitation } from "./source";

function text(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** An invitation as the server lists it, or null when it names nothing. */
export function readInvitation(raw: unknown): Invitation | null {
    if (!raw || typeof raw !== "object") return null;
    const said = raw as Record<string, unknown>;
    const id = text(said.id);
    if (!id) return null;
    return {
        id,
        email: text(said.email),
        role: text(said.role),
        status: text(said.status),
        expires_at: text(said.expires_at),
        accept_url: text(said.accept_url),
        emailed: typeof said.emailed === "boolean" ? said.emailed : null,
    };
}

/** The invitations still waiting on somebody, as the web keeps them. */
export function pendingOf(invitations: readonly Invitation[]): Invitation[] {
    return invitations.filter((invite) => invite.status === "PENDING");
}

/** What each role is for, in the phone's words: the web's `ROLES` blurbs
 *  call a Lemma a teammate. */
const ROLE_BLURB: Record<Role, string> = {
    ORG_OWNER: "Everything, including billing and removing people.",
    ORG_EDITOR: "Build and change Lemmas, connectors and keys.",
    ORG_MEMBER: "Work with the Lemmas that already exist.",
};

export function roleBlurb(role: string | null | undefined): string {
    return ROLE_BLURB[role as Role] ?? "";
}

export interface RoleChoice {
    value: Role;
    label: string;
    hint: string;
}

/** The roles somebody with `myRole` may hand out: an editor cannot make an
 *  owner, so ownership is not on their list. */
export function rolesToGive(myRole: string | null | undefined): RoleChoice[] {
    return ROLES.filter((entry) => canSetRole(myRole, entry.value))
        .map((entry) => ({ value: entry.value, label: entry.label, hint: roleBlurb(entry.value) }));
}

/** The roles on offer for one member: what may be given, and the role they
 *  already hold, so the choice they have is always on the list. */
export function rolesFor(myRole: string | null | undefined, memberRole: string | null | undefined): RoleChoice[] {
    return ROLES.filter((entry) => canSetRole(myRole, entry.value) || entry.value === memberRole)
        .map((entry) => ({ value: entry.value, label: entry.label, hint: roleBlurb(entry.value) }));
}

/** What the signed-in person may do to one member, and why not when they
 *  manage the organization but may not touch this row. */
export interface MemberReach {
    /** Whether their row opens at all: only for somebody who manages. */
    opens: boolean;
    changeRole: boolean;
    remove: boolean;
    /** Said in the member's sheet when nothing can be changed there. */
    why: string | null;
}

export function memberReach(
    myRole: string | null | undefined,
    members: readonly Member[],
    member: Member,
    myUserId: string | null | undefined,
): MemberReach {
    const manage = canManage(myRole);
    if (!manage) return { opens: false, changeRole: false, remove: false, why: null };
    const mine = Boolean(myUserId) && member.user_id === myUserId;
    /* An organization with no owner is one nobody can ever change again. */
    if (isLastOwner([...members], member.id)) {
        return {
            opens: true, changeRole: false, remove: false,
            why: (mine ? "You are" : memberName(member) + " is") + " the only owner. Make somebody else an owner first, so this organization always has one.",
        };
    }
    /* Nobody reaches over a person who holds authority they lack. */
    if (!canActOn(myRole, member.role)) {
        return { opens: true, changeRole: false, remove: false, why: "Only an owner can change an owner." };
    }
    return { opens: true, changeRole: true, remove: !mine, why: null };
}

/** The quieter words under a member's name: their address, and "you". */
export function memberHint(member: Member, mine: boolean): string {
    const email = memberEmail(member);
    return [mine ? "You" : "", email].filter(Boolean).join(" · ");
}

/** "4 people · 1 invited", "1 person". */
export function rosterWords(people: number | null, invited: number): string {
    const head = people === null ? "" : people + (people === 1 ? " person" : " people");
    return [head, invited > 0 ? invited + " invited" : ""].filter(Boolean).join(" · ");
}

/** Why an address cannot be invited, or null: what cannot work, then
 *  somebody already here or already asked. */
export function inviteWrong(email: string, members: readonly Member[], pending: readonly Invitation[]): string | null {
    return inviteProblem(email) ?? alreadyKnown(email, [...members], [...pending]);
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "Expires 31 October", "Expired", or "" when the server did not say. */
export function expiryWords(expiresAt: string | null | undefined, now = new Date()): string {
    if (!expiresAt) return "";
    const at = new Date(expiresAt);
    if (Number.isNaN(at.getTime())) return "";
    if (at.getTime() <= now.getTime()) return "Expired";
    const year = at.getFullYear() === now.getFullYear() ? "" : " " + at.getFullYear();
    return "Expires " + at.getDate() + " " + MONTHS[at.getMonth()] + year;
}

/** The quieter words under a pending invitation: the role it gives, when it
 *  runs out, and, for whoever can hand it over, that nobody was emailed. */
export function invitationHint(invite: Invitation, manage: boolean, now = new Date()): string {
    const role = ROLES.find((entry) => entry.value === invite.role)?.label ?? "";
    const parts = [role, expiryWords(invite.expires_at, now)];
    if (manage && invite.emailed === false) parts.push("Not emailed");
    return parts.filter(Boolean).join(" · ");
}

/** A domain as it is stored and shown: bare, lower case, no @. */
export function cleanDomain(typed: string): string {
    return typed.trim().replace(/^@+/, "").toLowerCase();
}

/** What picking a rule does: saves it, or asks for the domain first, since
 *  the domain rule with no domain would let nobody in. */
export function pickJoin(join: OrgJoin, policy: OrgJoinPolicy): { save: OrgJoin } | { askDomain: true } {
    if (policy === "domain" && !join.domain) return { askDomain: true };
    return { save: { ...join, policy } };
}
