import type { AboutLine } from "../types";
import { inviteProblem, memberName, type Member } from "@web/org/membership";
import { POD_ROLES, readPodRoles } from "@web/data/pod-roles";
import { count } from "../words";
import { sayJoinWords } from "@web/data/joining";

/** Who has access to a Lemma, and how somebody else gets it: what the
 *  screens decide, apart from drawing it. Pure, so `tests/` can hold it.
 *  The web's `shell/add-people.tsx`, `who-can-join.tsx` and `at-the-door.tsx`,
 *  less their markup. */

/** Somebody with access to a Lemma, as the roster says them. */
export interface PodPerson {
    /** The membership's id (`pod_member_id`). */
    id: string;
    /** The account behind it: what "is this me" is asked of. */
    userId: string | null;
    name: string;
    email: string | null;
    roles: string[];
    /** "Editor": what the roster calls somebody holding these roles. */
    role: string;
    /** "Can edit": what that lets them do. */
    can: string;
}

/** One row of `podMembers.list`, read the way the web's `membersOf` reads it. */
export function personOf(raw: unknown): PodPerson {
    const said = (raw ?? {}) as { pod_member_id?: string; user_id?: string; user_name?: string | null; email?: string; user_email?: string; roles?: string[] };
    const email = said.email ?? said.user_email ?? "";
    const name = said.user_name?.trim() || email || "Member";
    const roles = Array.isArray(said.roles) ? said.roles : [];
    return {
        id: said.pod_member_id ?? said.user_id ?? name,
        userId: said.user_id ?? null,
        name,
        email: email || null,
        roles,
        ...readPodRoles(roles),
    };
}

/** An invitation into the organization that names this Lemma, not yet taken up. */
export interface PendingInvite {
    id: string;
    email: string;
    /** The access it will give here, as the wire spells it (`POD_EDITOR`). */
    role: string | null;
}

/** The invitations still waiting on this Lemma, from the organization's list. */
export function pendingFor(rows: readonly unknown[], podId: string): PendingInvite[] {
    return rows
        .map((raw) => (raw ?? {}) as { id?: string; email?: string | null; status?: string | null; pod_id?: string | null; pod_role?: string | null })
        .filter((invite) => invite.status === "PENDING" && invite.pod_id === podId && Boolean(invite.id && invite.email))
        .map((invite) => ({ id: String(invite.id), email: String(invite.email), role: invite.pod_role ?? null }));
}

/** What the invitation came back as, for the inviter. */
export interface SentInvite {
    email: string;
    acceptUrl: string | null;
    /** False where this server does not send email; null when it never said. */
    emailed: boolean | null;
}

/** The access levels in the picker's order: the common grant first, the one
 *  that hands over who else gets in last (the web's `ROLES`). */
export const ACCESS_CHOICES = [...POD_ROLES.slice(1), POD_ROLES[0]].map((role) => ({
    value: role.value as string,
    label: role.label as string,
    hint: role.value === "POD_ADMIN" ? "Can edit, and add people" : null,
}));

/** What an access level is called, for a pending invitation's row. */
export function accessLabel(role: string | null): string {
    return POD_ROLES.find((one) => one.value === role)?.label ?? "Invited";
}

/** A member's access in the picker's short words ("Admin", "Can edit"):
 *  the strongest role they hold, or the roster's word for a custom one. */
export function accessOf(person: Pick<PodPerson, "roles" | "role">): string {
    return POD_ROLES.find((one) => person.roles.includes(one.value))?.label ?? person.role;
}

/** Somebody in the organization who could be given access here. */
export interface Candidate {
    /** Their organization membership's id, which adding takes. */
    id: string;
    name: string;
    email: string;
    /** "editor", "member": what they are in the organization. */
    orgRole: string;
}

function keyOf(person: { email: string | null; name: string }): string {
    return (person.email || person.name).toLowerCase();
}

/** The organization's people who have no access here yet, known by address
 *  as the web knows them, the name only where there is no address. */
export function candidatesFrom(members: readonly Member[], people: readonly PodPerson[]): Candidate[] {
    const already = new Set(people.map(keyOf));
    return members
        .map((member) => ({
            id: member.id,
            name: memberName(member),
            email: member.user?.email ?? member.email ?? "",
            orgRole: String(member.role ?? "").replace(/^ORG_/, "").toLowerCase(),
        }))
        .filter((candidate) => !already.has(keyOf(candidate)));
}

/** What the box at the top of Add people would do with what was typed:
 *  add somebody already in the organization, invite a new address, or
 *  nothing; who matches; and the line under the box that says why the
 *  button is off before anybody presses it. */
export interface AddPlan {
    typed: string;
    shown: Candidate[];
    exact: Candidate | null;
    action: "add" | "invite" | null;
    hint: string | null;
}

export function addPlan({ query, candidates, people, pending, orgRead, lemma, org }: {
    query: string;
    candidates: readonly Candidate[];
    people: readonly PodPerson[];
    pending: readonly PendingInvite[];
    /** Whether the organization's people have been read. */
    orgRead: boolean;
    lemma: string;
    org: string;
}): AddPlan {
    const typed = query.trim().toLowerCase();
    const shown = typed
        ? candidates.filter((one) => one.email.toLowerCase().includes(typed) || one.name.toLowerCase().includes(typed))
        : [...candidates];
    const exact = typed ? candidates.find((one) => (one.email || one.name).toLowerCase() === typed) ?? null : null;
    const invited = typed !== "" && pending.some((invite) => invite.email.toLowerCase() === typed);
    const inHere = typed !== "" && people.some((person) => keyOf(person) === typed);
    const looksLikeEmail = typed !== "" && inviteProblem(typed) === null;
    const action = exact ? "add" : looksLikeEmail && !inHere && !invited ? "invite" : null;
    const hint = inHere
        ? `They already have access to ${lemma}.`
        : invited
          ? `They have already been invited to ${lemma}.`
          : !typed && orgRead && candidates.length === 0 && pending.length === 0
            ? `Everyone in ${org.trim() || "this organization"} already has access. Add someone new by email.`
            : null;
    return { typed, shown, exact, action, hint };
}

/** What pressing the button says when it would do nothing: why an address
 *  is not one, or null when there is nothing to say. */
export function submitProblem(plan: AddPlan, query: string): string | null {
    if (plan.action || !plan.typed || plan.shown.length > 0) return null;
    return inviteProblem(query) ?? plan.hint;
}

/** A refusal to move the door, in this app's words: the picker's own names
 *  for the three rules rather than the wire's, and "Lemma" where the server
 *  says "pod". */
export function sayJoinRefusal(text: string, org: string): string {
    return sayJoinWords(text, org).replace(/\bpods\b/gi, "Lemmas").replace(/\bpod\b/gi, "Lemma");
}

/** "Someone is asking to join Support", "3 people are asking to join Support". */
export function askingLine(asking: number, lemma: string): string {
    return `${asking === 1 ? "Someone is asking" : `${asking} people are asking`} to join ${lemma}`;
}

/** The quiet half of an ask's row: the address beside a name (two
 *  colleagues can share one), and when they asked. */
export function askedLine(request: { name: string; email: string; askedAt: string }, now: number = Date.now()): string {
    const mail = request.name && request.email ? request.email : "";
    const when = request.askedAt ? agoOf(request.askedAt, now) : "";
    return [mail, when].filter(Boolean).join(" · ");
}

/** The Settings tab's line: how many have access, and how many are asking,
 *  with the dot when somebody is. */
export function peopleLine(people: number | null, asking: number): AboutLine {
    if (people === null) return { text: null, needs: asking > 0 };
    return { text: count(people, "person", "people") + (asking ? ` · ${asking} asking to join` : ""), needs: asking > 0 };
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "12m ago", "3d ago", "2w ago" (the web's `agoOf`). */
export function agoOf(iso: string, now: number = Date.now()): string {
    if (!iso) return "";
    const at = new Date(iso).getTime();
    if (Number.isNaN(at)) return "";
    const gap = now - at;
    if (gap < 0) return "in a moment";
    if (gap < MINUTE) return "just now";
    if (gap < HOUR) return `${Math.floor(gap / MINUTE)}m ago`;
    if (gap < DAY) return `${Math.floor(gap / HOUR)}h ago`;
    if (gap < 7 * DAY) return `${Math.floor(gap / DAY)}d ago`;
    return `${Math.floor(gap / (7 * DAY))}w ago`;
}
