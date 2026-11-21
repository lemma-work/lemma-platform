"use client";

import { useEffect, useId, useRef, useState, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { saidAbout, type Member as PodMember, type Org, type Pod } from "@/data";
import { copyText } from "@/desktop/clipboard";
import { ROLES as ORG_ROLES, canActOn, canManage, canSetRole, roleLabel, unsentInvitation, type Role } from "@/org/membership";
import { lemma } from "@/session/client";
import { TeammateFace } from "@/space/teammate-face";
import { ConfirmRemove, LEMMA_ROLES, LEMMA_ROLE_FIRST, lemmaRoleSaid, usePodManagers, type OrgInvite } from "./invite-dialog";
import { outcomeView, type Go } from "./model";
import { Avatar, Icon, to } from "./parts";

/* ── a person, as the People page and its sheet see them ──────────────── */

export interface PersonLemma {
    pod: Pod;
    /** Their membership there; null for the Lemma an invitation starts them in. */
    member: PodMember | null;
    /** The role, as the wire spells it, where one role stands for it. */
    role: string | null;
    /** The role in the picker's words. */
    said: string;
}

export interface Person {
    /** `m:` and the membership for somebody in, `i:` and the address for
     *  an invitation, so sending one again keeps its row and its sheet. */
    key: string;
    kind: "member" | "invited" | "lapsed";
    /** Their name, or their address where nothing else is known. */
    name: string;
    named: boolean;
    email: string;
    /** Their role in the organization (`ORG_OWNER`), null in the sample. */
    role: string | null;
    memberId: string | null;
    /** When they joined, or when they were asked. */
    since: string | null;
    expires: string | null;
    invite: OrgInvite | null;
    mine: boolean;
    lemmas: PersonLemma[];
}

/** What the reader is in the organization, which decides what they are
 *  offered on everybody else. */
export interface Standing {
    /** False in the sample, where nothing changes. */
    live: boolean;
    myRole: string | null;
    owners: number;
    orgName: string;
}

const DAY = 24 * 60 * 60_000;

function dayOf(iso: string): string {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const sameYear = date.getFullYear() === new Date().getFullYear();
    return date.toLocaleDateString([], sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
}

/** Where somebody stands, in a few words: joined when, or an invitation's
 *  days left, or how long ago it lapsed. */
export function statusOf(person: Person, now: number = Date.now()): { said: string; tone: "plain" | "wait" | "gone" } {
    if (person.kind === "member") {
        const day = person.since ? dayOf(person.since) : "";
        return { said: day ? "Joined " + day : "", tone: "plain" };
    }
    const at = person.expires ? Date.parse(person.expires) : Number.NaN;
    if (person.kind === "invited") {
        if (Number.isNaN(at)) return { said: "Invited", tone: "wait" };
        const days = Math.ceil((at - now) / DAY);
        return { said: days <= 0 ? "Invited · lapses today" : days === 1 ? "Invited · lapses tomorrow" : `Invited · lapses in ${days} days`, tone: "wait" };
    }
    if (Number.isNaN(at)) return { said: "Invitation lapsed", tone: "gone" };
    const ago = Math.floor((now - at) / DAY);
    return { said: ago <= 0 ? "Invitation lapsed today" : ago === 1 ? "Invitation lapsed yesterday" : `Invitation lapsed ${ago} days ago`, tone: "gone" };
}

/** Whether the reader may change this person's role in the organization,
 *  or take them out of it: the same question, since taking a role away is
 *  the mirror of handing one out. Nobody changes their own here, nobody
 *  reaches over authority they lack, and the last owner stays. */
export function mayChange(person: Person, standing: Standing): boolean {
    return standing.live
        && person.kind === "member"
        && !person.mine
        && canManage(standing.myRole)
        && canActOn(standing.myRole, person.role)
        && !(person.role === "ORG_OWNER" && standing.owners <= 1);
}

/** The roles the reader can pick between for this person. */
export function roleChoices(standing: Standing, current: string | null) {
    return ORG_ROLES.filter((entry) => canSetRole(standing.myRole, entry.value) || entry.value === current);
}

/** Why the reader cannot change this person, said where the control would
 *  be, with who can. */
export function whyNot(person: Person, standing: Standing, about: "role" | "remove"): string | null {
    const org = standing.orgName;
    if (!standing.live) return "This is a sample, so nothing changes from here.";
    if (person.kind !== "member") return about === "role" ? "An invitation’s role is set when it’s sent." : null;
    if (person.mine) {
        if (person.role === "ORG_OWNER") return standing.owners <= 1 ? `You’re the only owner of ${org}, so you stay one.` : `You own ${org}. Another owner can change that.`;
        return `You’re ${withArticle(roleLabel(person.role))} in ${org}. An owner or editor can change that.`;
    }
    if (!canManage(standing.myRole)) return about === "role" ? `Only owners and editors of ${org} can change roles.` : `Only owners and editors of ${org} can take people out.`;
    if (!canActOn(standing.myRole, person.role)) return about === "role" ? "Only an owner can change another owner." : "Only an owner can take another owner out.";
    if (person.role === "ORG_OWNER" && standing.owners <= 1) return `${org} needs an owner, and this is its only one.`;
    return null;
}

/** "a Member", "an Editor", "an Owner". */
export function withArticle(label: string): string {
    return (/^[AEIOU]/i.test(label) ? "an " : "a ") + label;
}

/* ── the sheet ────────────────────────────────────────────────────────── */

type Confirming = "remove" | "withdraw" | null;

/** One person, opened from the People table: a sheet from the right edge,
 *  over a light veil, so the page they were opened from stays put.
 *
 *  It holds everything about where they stand: their role in the
 *  organization, each Lemma they are in with their role there, a way into
 *  the ones they are not in, and, at its foot, taking them out. Every
 *  control is offered only where the server will allow it; elsewhere the
 *  sheet says why and who can, in the place the control would be.
 *
 *  An invitation is the same sheet with its own foot: its link to copy, or
 *  sending it again once it has lapsed, and withdrawing it. Both endings
 *  that take somebody away ask first, in a small dialog. */
export function PersonSheet({ person, org, pods, standing, go, onClose, onGone }: {
    person: Person;
    org: Org;
    pods: readonly Pod[];
    standing: Standing;
    go: Go;
    onClose: () => void;
    /** Called when they are gone from the page, with what happened. */
    onGone: (said: string) => void;
}) {
    const queryClient = useQueryClient();
    const panel = useRef<HTMLElement>(null);
    const titleId = useId();
    const managers = usePodManagers(pods);
    const [busy, setBusy] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [confirming, setConfirming] = useState<Confirming>(null);
    const [link, setLink] = useState<{ url: string; state: "copied" | "failed" | "shown" } | null>(null);
    const [sent, setSent] = useState<string | null>(null);
    const held = useRef<Confirming>(null);
    held.current = confirming;
    const closeRef = useRef(onClose);
    closeRef.current = onClose;

    /* Focus moves in, the keyboard stays in while it is open, Escape closes
       it, and focus goes back to whatever opened it. A dialog over it (the
       confirm) answers its own keys first and stops them there. */
    useEffect(() => {
        const previous = document.activeElement as HTMLElement | null;
        panel.current?.querySelector<HTMLElement>(".ksheet__x")?.focus();
        const onKey = (event: KeyboardEvent) => {
            if (held.current || event.defaultPrevented) return;
            if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
            if (event.key !== "Tab" || !panel.current) return;
            const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled])')).filter((el) => el.getClientRects().length > 0);
            const first = focusable[0], last = focusable.at(-1);
            if (!first) { event.preventDefault(); return; }
            if (!panel.current.contains(document.activeElement)) { event.preventDefault(); first.focus(); return; }
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        };
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("keydown", onKey);
            if (previous?.isConnected) previous.focus();
        };
    }, []);

    const status = statusOf(person);
    const changeable = mayChange(person, standing);
    const isMember = person.kind === "member";
    const inIds = new Set(person.lemmas.map((one) => one.pod.id));
    const addable = isMember && standing.live && person.memberId
        ? pods.filter((pod) => !pod.everyone && !inIds.has(pod.id) && managers.get(pod.id)?.allowed)
        : [];
    const someShut = isMember && standing.live && pods.some((pod) => !pod.everyone && !inIds.has(pod.id) && managers.get(pod.id)?.answered && !managers.get(pod.id)?.allowed);
    const manage = standing.live && canManage(standing.myRole);

    async function run(key: string, work: () => Promise<unknown>, fallback: string) {
        setBusy(key);
        setProblem(null);
        try {
            await work();
        } catch (error) {
            setProblem(saidAbout(error, fallback));
        } finally {
            setBusy(null);
        }
    }

    async function rosterChanged(podIds: readonly string[]) {
        await Promise.all([
            ...podIds.map((podId) => queryClient.invalidateQueries({ queryKey: ["pod-detail", podId] })),
            queryClient.invalidateQueries({ queryKey: ["pods", org.id] }),
        ]);
    }

    const setOrgRole = (next: string) => run("role", async () => {
        await lemma().organizations.members.updateRole(org.id, person.memberId as string, next as never);
        await queryClient.invalidateQueries({ queryKey: ["org-members", org.id] });
    }, "Their role didn’t change.");

    const setPodRole = (one: PersonLemma, next: string) => run("pod-role:" + one.pod.id, async () => {
        await lemma(one.pod.id).podMembers.updateRoles(one.pod.id, (one.member as PodMember).id, [next]);
        await rosterChanged([one.pod.id]);
    }, `Their role in ${one.pod.name} didn’t change.`);

    const takeOut = (one: PersonLemma) => run("out:" + one.pod.id, async () => {
        await lemma(one.pod.id).podMembers.remove(one.pod.id, (one.member as PodMember).id);
        await rosterChanged([one.pod.id]);
    }, `Couldn’t take them out of ${one.pod.name}.`);

    const addTo = (pod: Pod) => run("in:" + pod.id, async () => {
        await lemma(pod.id).podMembers.add(pod.id, { organization_member_id: person.memberId as string, roles: [LEMMA_ROLE_FIRST] });
        await rosterChanged([pod.id]);
    }, `Couldn’t add them to ${pod.name}.`);

    const sendAgain = () => run("again", async () => {
        const invite = person.invite as OrgInvite;
        const startIn = invite.pod_id && pods.some((pod) => pod.id === invite.pod_id) ? invite.pod_id : null;
        const created = (await lemma().organizations.invitations.invite(org.id, {
            email: person.email,
            role: (invite.role ?? "ORG_MEMBER") as never,
            ...(startIn ? { pod_id: startIn, pod_role: invite.pod_role ?? LEMMA_ROLE_FIRST, redirect_uri: "/l/" + encodeURIComponent(startIn) } : {}),
        })) as OrgInvite;
        await queryClient.invalidateQueries({ queryKey: ["org-invitations", org.id] });
        const notSent = unsentInvitation(created);
        if (notSent?.link) setLink({ url: notSent.link, state: "shown" });
        setSent(notSent ? notSent.said : "Sent again. The new link works for 7 days.");
    }, "That invitation was not sent.");

    function copyLink(url: string) {
        copyText(url).then(() => setLink({ url, state: "copied" }), () => setLink({ url, state: "failed" }));
    }

    async function removeFromOrg() {
        await lemma().organizations.members.remove(org.id, person.memberId as string);
        await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["org-members", org.id] }),
            rosterChanged(person.lemmas.map((one) => one.pod.id)),
        ]);
        onGone(`${person.name} is out of ${org.name}.`);
    }

    async function withdraw() {
        await lemma().organizations.invitations.revoke((person.invite as OrgInvite).id);
        await queryClient.invalidateQueries({ queryKey: ["org-invitations", org.id] });
        onGone(`The invitation to ${person.email} is withdrawn.`);
    }

    const lemmaNames = person.lemmas.map((one) => one.pod.name);
    const across = lemmaNames.length === 0 ? org.name
        : lemmaNames.length === 1 ? lemmaNames[0]
        : lemmaNames.slice(0, -1).join(", ") + " and " + lemmaNames.at(-1);

    const why = whyNot(person, standing, "role");
    const whyRemove = whyNot(person, standing, "remove");

    const foot = !standing.live ? (
        <p className="ox-people-sheet__note">This is a sample, so nothing changes from here.</p>
    ) : isMember ? (
        changeable ? (
            <button type="button" className="b b--m b--secondary b--red" onClick={() => setConfirming("remove")}>
                Remove from {org.name}
            </button>
        ) : <p className="ox-people-sheet__note">{whyRemove}</p>
    ) : !manage ? (
        <p className="ox-people-sheet__note">Only owners and editors of {org.name} can change invitations.</p>
    ) : (
        <div className="ox-people-sheet__acts">
            {person.kind === "invited" && person.invite?.accept_url && (
                <button type="button" className="b b--m b--secondary" onClick={() => copyLink(person.invite?.accept_url as string)}>
                    <Icon name={link?.state === "copied" ? "check" : "link"} size={16} />
                    {link?.state === "copied" ? "Copied" : "Copy their link"}
                </button>
            )}
            {person.kind === "lapsed" && canSetRole(standing.myRole, (person.invite?.role ?? "ORG_MEMBER") as Role) && (
                <button type="button" className="b b--m b--secondary" disabled={busy !== null} onClick={() => void sendAgain()}>
                    {busy === "again" ? "Sending…" : "Send again"}
                </button>
            )}
            {person.kind === "invited" && (
                <button type="button" className="b b--m b--secondary b--red" onClick={() => setConfirming("withdraw")}>
                    Withdraw the invitation
                </button>
            )}
        </div>
    );

    return createPortal(
        <>
            <div className="ksheet-veil" aria-hidden="true" onMouseDown={() => { if (!confirming) onClose(); }} />
            <aside className="ksheet ox-people-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel}>
                <header className="ksheet__head">
                    <Avatar name={person.name} size={56} />
                    <button type="button" className="ksheet__x b b--m b--quiet b--icon" aria-label="Close" onClick={onClose}>
                        <Icon name="close" size={18} />
                    </button>
                    <h2 id={titleId}>{person.name}{person.mine && <span className="ox-people__you">You</span>}</h2>
                    <p>{[person.named ? person.email : "", status.said].filter(Boolean).join(" · ")}</p>
                </header>

                <div className="ksheet__body">
                    <div className="ox-people-sheet__kv">
                        <span className="ox-people-sheet__k">Role in {org.name}</span>
                        {changeable ? (
                            <span className="ox-people__pick">
                                <select
                                    aria-label={`${person.name}’s role in ${org.name}`}
                                    value={person.role ?? "ORG_MEMBER"}
                                    disabled={busy !== null}
                                    onChange={(event) => void setOrgRole(event.target.value)}
                                >
                                    {roleChoices(standing, person.role).map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
                                </select>
                                <Icon name="chevron" size={12} />
                            </span>
                        ) : (
                            <span className="ox-people__fixed">{person.role ? roleLabel(person.role) : "—"}</span>
                        )}
                    </div>
                    {!changeable && why && standing.live && person.kind === "member" && !person.mine && (
                        <p className="ox-people-sheet__note">{why}</p>
                    )}

                    <section className="ox-people-sheet__lemmas" aria-labelledby={titleId + "-lemmas"}>
                        <h3 className="ox-people-sheet__sub" id={titleId + "-lemmas"}>{isMember ? "Lemmas" : "Starts in"}</h3>
                        {person.lemmas.length === 0 && (
                            <p className="ox-people-sheet__note">{isMember ? "Not in any Lemma yet." : "No Lemma yet. Once they join, you can add them to one."}</p>
                        )}
                        {person.lemmas.map((one) => (
                            <LemmaRow
                                key={one.pod.id}
                                one={one}
                                person={person}
                                allowed={standing.live && Boolean(managers.get(one.pod.id)?.allowed)}
                                busy={busy}
                                go={go}
                                onClose={onClose}
                                onRole={(next) => void setPodRole(one, next)}
                                onOut={() => void takeOut(one)}
                            />
                        ))}
                    </section>

                    {addable.length > 0 && (
                        <div className="ox-people-sheet__adds">
                            {addable.map((pod) => (
                                <button key={pod.id} type="button" className="b b--s b--secondary" disabled={busy !== null} onClick={() => void addTo(pod)}>
                                    <Icon name="plus" size={15} />
                                    {busy === "in:" + pod.id ? "Adding…" : "Add to " + pod.name}
                                </button>
                            ))}
                        </div>
                    )}
                    {someShut && <p className="ox-people-sheet__note">Only a Lemma’s admins can add people to it.</p>}

                    {sent && <p className="ox-people-sheet__note" role="status">{sent}</p>}
                    {link && link.state !== "copied" && (
                        <div className="ox-people-sheet__link">
                            {link.state === "failed" && <p className="ox-people-sheet__note" role="alert">Couldn’t copy it. Select the link and copy it yourself.</p>}
                            <input
                                className="kinput"
                                readOnly
                                value={link.url}
                                aria-label={"Invitation link for " + person.email}
                                onFocus={(event) => event.target.select()}
                            />
                            {link.state === "shown" && (
                                <button type="button" className="b b--s b--secondary" onClick={() => copyLink(link.url)}>
                                    <Icon name="link" size={15} />
                                    Copy
                                </button>
                            )}
                        </div>
                    )}
                    {problem && <p className="ox-people-sheet__problem" role="alert">{problem}</p>}
                </div>

                <footer className="ksheet__foot">{foot}</footer>
            </aside>

            {confirming === "remove" && (
                <ConfirmRemove
                    title={`Remove ${person.name} from ${org.name}?`}
                    line={`They lose access to ${across} straight away.`}
                    action="Remove"
                    busyLabel="Removing…"
                    onConfirm={removeFromOrg}
                    onClose={() => setConfirming(null)}
                />
            )}
            {confirming === "withdraw" && (
                <ConfirmRemove
                    title={`Withdraw ${person.email}’s invitation?`}
                    line="Their link stops working. You can invite them again any time."
                    action="Withdraw"
                    busyLabel="Withdrawing…"
                    onConfirm={withdraw}
                    onClose={() => setConfirming(null)}
                />
            )}
        </>,
        document.body,
    );
}

/** One Lemma in the sheet: its face and name, which opens it, their role
 *  there, and taking them out. A Lemma's last admin keeps the role and the
 *  place, since a Lemma nobody can manage is one nobody can fix; the
 *  organization's own Lemma holds everyone, so nobody is taken out of it. */
function LemmaRow({ one, person, allowed, busy, go, onClose, onRole, onOut }: {
    one: PersonLemma;
    person: Person;
    allowed: boolean;
    busy: string | null;
    go: Go;
    onClose: () => void;
    onRole: (next: string) => void;
    onOut: () => void;
}) {
    const admins = one.pod.members.filter((member) => member.kind === "person" && member.podRole === "POD_ADMIN").length;
    const lastAdmin = one.role === "POD_ADMIN" && admins <= 1;
    const known = LEMMA_ROLES.some((entry) => entry.value === one.role);
    const editable = allowed && Boolean(one.member) && known && !lastAdmin;
    const removable = allowed && Boolean(one.member) && !lastAdmin && !one.pod.everyone;
    const link = to(outcomeView(one.pod.id), go);
    const open = (event: MouseEvent<HTMLAnchorElement>) => {
        link.onClick(event);
        if (event.defaultPrevented) onClose();
    };
    return (
        <div className="ox-people-sheet__lemma" data-busy={busy?.endsWith(":" + one.pod.id) ? "" : undefined}>
            <TeammateFace pod={one.pod} size={28} />
            <a className="ox-people-sheet__lname" href={link.href} onClick={open}>{one.pod.name}</a>
            {editable ? (
                <span className="ox-people__pick">
                    <select
                        aria-label={`${person.name}’s role in ${one.pod.name}`}
                        value={one.role ?? ""}
                        disabled={busy !== null}
                        onChange={(event) => onRole(event.target.value)}
                    >
                        {LEMMA_ROLES.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
                    </select>
                    <Icon name="chevron" size={12} />
                </span>
            ) : (
                <span className="ox-people__fixed" title={lastAdmin && allowed ? `The only admin of ${one.pod.name}` : undefined}>
                    {one.member ? one.said : lemmaRoleSaid(one.role)}
                </span>
            )}
            {removable ? (
                <button
                    type="button"
                    className="b b--s b--quiet b--icon"
                    aria-label={`Take ${person.name} out of ${one.pod.name}`}
                    disabled={busy !== null}
                    onClick={onOut}
                >
                    <Icon name="close" size={15} />
                </button>
            ) : <span className="ox-people-sheet__gap" aria-hidden="true" />}
        </div>
    );
}
