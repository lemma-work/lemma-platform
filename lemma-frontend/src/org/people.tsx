"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { lemma } from "@/session/client";
import { isForbidden } from "@/session/auth-state";
import { CloseIcon, KeyIcon } from "@/ui/icons";
import { copyText } from "@/desktop/clipboard";
import { openSettings } from "@/desktop/open-settings";
import { useThisMacAvailability } from "@/desktop/this-mac-settings";
import { capitalised, useThisComputer } from "@/desktop/this-computer";
import { isLocalDeployment } from "@/site/config";
import {
    ROLES, alreadyKnown, canManage, canSetJoinPolicy, canSetRole, inviteProblem, isLastOwner,
    linkOnlyOpensHere, memberEmail, memberName, roleLabel, unsentInvitation, type Member, type Role,
} from "./membership";
import { WhoCanJoinOrg } from "./who-can-join-org";

/** Who is here, and how somebody else gets to be.
 *
 *  It was two read-only tables, which answered "who is in this organization"
 *  and nothing else — so the answer to "how do I add someone" was to leave and
 *  do it in another app. Everything needed was already on the client: invite,
 *  revoke, change a role, remove.
 *
 *  What is offered depends on what the caller may actually do. The server
 *  decides and will refuse the rest; this is so the screen does not show a
 *  button that is always going to fail.
 */

interface Listish { items?: unknown[] }
function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    return (value as Listish)?.items ?? [];
}

interface Invite {
    id: string;
    email?: string | null;
    role?: string | null;
    status?: string | null;
    expires_at?: string | null;
    accept_url?: string | null;
    emailed?: boolean | null;
}

/** An invitation link, with a way to copy it. The copy can fail -- a shared
 *  address on the LAN is not a secure context -- and says so rather than
 *  pretending, because the link is then the only thing the person needs. */
function CopyLink({ link, label = "Copy link" }: { link: string; label?: string }) {
    const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
    return (
        <button
            type="button"
            className="linkish"
            onClick={() => {
                copyText(link).then(() => setState("copied"), () => setState("failed"));
            }}
        >
            {state === "copied" ? "Copied" : state === "failed" ? "Couldn't copy — select the link" : label}
        </button>
    );
}

/** Beside a link that only opens on this computer: why sending it would do
 *  nothing yet, and the switch that fixes it. On a local install only — a
 *  hosted workspace never builds such a link. */
function OnlyOpensHere({ link, machine, canOpenSharing }: { link: string | null | undefined; machine: string; canOpenSharing: boolean }) {
    if (!isLocalDeployment() || !linkOnlyOpensHere(link)) return null;
    return (
        <p className="invite__hint">
            People on other devices can’t open this link until you turn on Sharing on {machine}.{" "}
            {canOpenSharing && (
                <button type="button" className="linkish" onClick={() => openSettings("this-mac-sharing")}>
                    Open Sharing
                </button>
            )}
        </p>
    );
}

export function PeopleSection({ orgId }: { orgId: string }) {
    const queryClient = useQueryClient();
    const [email, setEmail] = useState("");
    const [role, setRole] = useState<Role>("ORG_MEMBER");
    const [problem, setProblem] = useState<string | null>(null);
    const [unsent, setUnsent] = useState<ReturnType<typeof unsentInvitation>>(null);
    /* On Lemma Desktop the person reading is the one who can set email up. */
    const thisMac = useThisMacAvailability();
    const noun = useThisComputer();
    const machine = capitalised(noun);
    /* Organization membership is not part of the sample source — there is no
       organization behind it to have members — so there is nothing to ask. */
    const enabled = source.label !== "sample";

    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled,
        staleTime: 5 * 60_000,
    });

    const members = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: () => lemma().organizations.members.list(orgId, { limit: 100 }),
        enabled,
    });

    const invitations = useQuery({
        queryKey: ["org-invitations", orgId],
        queryFn: () => lemma().organizations.invitations.list(orgId, { limit: 50 }),
        enabled,
    });

    const people = useMemo(() => itemsOf(members.data) as Member[], [members.data]);
    const pending = useMemo(
        () => (itemsOf(invitations.data) as Invite[]).filter((invite) => invite.status === "PENDING"),
        [invitations.data],
    );
    /* My own role decides what this screen offers, and it is only knowable by
       finding myself in the list — the members endpoint is the only thing that
       says what I am here. */
    const myRole = people.find((member) => member.user_id === me.data?.id)?.role ?? null;
    const manage = canManage(myRole);

    function refresh() {
        void queryClient.invalidateQueries({ queryKey: ["org-members", orgId] });
        void queryClient.invalidateQueries({ queryKey: ["org-invitations", orgId] });
    }

    const invite = useMutation({
        mutationFn: () => lemma().organizations.invitations.invite(orgId, { email: email.trim(), role: role as never }),
        onSuccess: (created: Invite) => {
            setEmail(""); setProblem(null); setUnsent(unsentInvitation(created)); refresh();
        },
        onError: (error: Error) => setProblem(error.message || "That invitation was not sent."),
    });

    const revoke = useMutation({
        mutationFn: (invitationId: string) => lemma().organizations.invitations.revoke(invitationId),
        onSuccess: refresh,
        onError: (error: Error) => setProblem(error.message || "That invitation was not withdrawn."),
    });

    const setMemberRole = useMutation({
        mutationFn: ({ memberId, next }: { memberId: string; next: Role }) =>
            lemma().organizations.members.updateRole(orgId, memberId, next as never),
        onSuccess: refresh,
        onError: (error: Error) => setProblem(error.message || "That role was not changed."),
    });

    const remove = useMutation({
        mutationFn: (memberId: string) => lemma().organizations.members.remove(orgId, memberId),
        onSuccess: refresh,
        onError: (error: Error) => setProblem(error.message || "They were not removed."),
    });

    function send() {
        const wrong = inviteProblem(email) ?? alreadyKnown(email, people, pending);
        if (wrong) { setProblem(wrong); return; }
        setProblem(null);
        setUnsent(null);
        invite.mutate();
    }

    /* The door is drawn in the sample too, where the roster is not. There is
       no real organization behind this source to have members of — but the
       rule for getting into one is a control with three states and a field,
       and a control nobody can look at is a control nobody can judge. */
    const door = (
        <WhoCanJoinOrg
            orgId={orgId}
            mayChange={enabled ? (members.isSuccess ? canSetJoinPolicy(myRole) : null) : true}
        />
    );

    if (!enabled) {
        return (
            <div className="section">
                <p className="section__meta">The sample workspace has no real organization to show members of.</p>
                {door}
            </div>
        );
    }

    return (
        <div className="section">
            {/* Asking somebody in is the first row of the page, not a box that
                appears when you find the button for it. A screen whose only
                permanent content is the people already here is a screen that
                never suggests there could be more. */}
            {manage && (
                <form className="invite" onSubmit={(event) => { event.preventDefault(); send(); }}>
                    <div className="invite__row">
                        <input
                            className="invite__email"
                            value={email}
                            onChange={(event) => { setEmail(event.target.value); setProblem(null); }}
                            placeholder="Invite someone by email"
                            inputMode="email"
                            aria-label="Email address"
                        />
                        <select value={role} onChange={(event) => setRole(event.target.value as Role)} aria-label="Role">
                            {/* An editor cannot hand out ownership, so it is not
                                in the list they are choosing from. */}
                            {ROLES.filter((entry) => canSetRole(myRole, entry.value)).map((entry) => (
                                <option key={entry.value} value={entry.value}>{entry.label}</option>
                            ))}
                        </select>
                        <button className="btn btn--primary" type="submit" disabled={invite.isPending || !email.trim()}>
                            {invite.isPending ? "Sending…" : "Invite"}
                        </button>
                    </div>
                    {/* What the chosen role can do, under the thing that chose
                        it. Under the whole form instead, it reads as a
                        sentence about nothing in particular. */}
                    <p className="invite__hint" role={problem ? "alert" : undefined} data-bad={Boolean(problem)}>
                        {problem ?? ROLES.find((entry) => entry.value === role)?.blurb}
                    </p>
                    {unsent && (
                        <div className="invite__unsent" role="status">
                            <p className="invite__hint">{unsent.said}</p>
                            {unsent.link && (
                                <div className="invite__row">
                                    <input
                                        className="invite__email"
                                        readOnly
                                        value={unsent.link}
                                        aria-label={"Invitation link for " + unsent.to}
                                        onFocus={(event) => event.target.select()}
                                    />
                                    <CopyLink link={unsent.link} />
                                </div>
                            )}
                            <OnlyOpensHere link={unsent.link} machine={noun} canOpenSharing={thisMac === "shown"} />
                            {thisMac === "shown" && (
                                <button type="button" className="linkish thismac-setup" onClick={() => openSettings("this-mac-setup", "email")}>
                                    <KeyIcon size={13} /> Set up email in {machine} → Server setup
                                </button>
                            )}
                        </div>
                    )}
                </form>
            )}

            <p className="section__meta">
                {members.isSuccess ? people.length + (people.length === 1 ? " person" : " people") : ""}
                {pending.length > 0 ? " · " + pending.length + " invited" : ""}
            </p>

            {members.isPending && <p className="empty-row">Reading…</p>}
            {members.isError && (
                <p className="empty-row">
                    {isForbidden(members.error) ? "You are not a member of this organization." : "Not readable from here."}
                </p>
            )}

            {members.isSuccess && (
                <ul className="people">
                    {people.map((member) => {
                        const mine = member.user_id === me.data?.id;
                        const last = isLastOwner(people, member.id);
                        const email2 = memberEmail(member);
                        return (
                            <li className="person" key={member.id}>
                                <div className="person__who">
                                    <strong>{memberName(member)}{mine && <span className="person__you">you</span>}</strong>
                                    {email2 && <small>{email2}</small>}
                                </div>
                                {manage && !last ? (
                                    <select
                                        className="person__role"
                                        value={member.role ?? "ORG_MEMBER"}
                                        disabled={setMemberRole.isPending}
                                        onChange={(event) => setMemberRole.mutate({ memberId: member.id, next: event.target.value as Role })}
                                        aria-label={"Role for " + memberName(member)}
                                    >
                                        {ROLES.filter((entry) => canSetRole(myRole, entry.value) || entry.value === member.role).map((entry) => (
                                            <option key={entry.value} value={entry.value}>{entry.label}</option>
                                        ))}
                                    </select>
                                ) : (
                                    <span className="person__role person__role--fixed" title={last ? "The last owner cannot be changed" : undefined}>
                                        {roleLabel(member.role)}
                                    </span>
                                )}
                                {/* Removing the last owner would leave an
                                    organization nobody can ever change again. */}
                                {(!manage || last || mine) && <span className="person__gap" aria-hidden="true" />}
                                {manage && !last && !mine && (
                                    <button
                                        className="person__remove"
                                        title={"Remove " + memberName(member)}
                                        aria-label={"Remove " + memberName(member)}
                                        disabled={remove.isPending}
                                        onClick={() => {
                                            if (window.confirm("Remove " + memberName(member) + " from this organization?")) {
                                                remove.mutate(member.id);
                                            }
                                        }}
                                    ><CloseIcon size={15} /></button>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}

            {pending.length > 0 && (
                <>
                    <p className="people__label">Invited, not joined yet</p>
                    <ul className="people">
                        {pending.map((item) => (
                            <li className="person" key={item.id}>
                                <div className="person__who">
                                    <strong>{item.email ?? "—"}</strong>
                                    {item.expires_at && <small>expires {String(item.expires_at).slice(0, 10)}</small>}
                                    {manage && item.emailed === false && (
                                        <OnlyOpensHere link={item.accept_url} machine={noun} canOpenSharing={thisMac === "shown"} />
                                    )}
                                </div>
                                {/* Where nobody was emailed, the link is the
                                    invitation; it stays reachable after the
                                    notice above has gone. */}
                                {manage && item.emailed === false && item.accept_url && <CopyLink link={item.accept_url} />}

                                <span className="person__role person__role--fixed">{roleLabel(item.role)}</span>
                                {!manage && <span className="person__gap" aria-hidden="true" />}
                                {manage && (
                                    <button
                                        className="person__remove"
                                        title="Withdraw this invitation"
                                        aria-label={"Withdraw the invitation to " + (item.email ?? "them")}
                                        disabled={revoke.isPending}
                                        onClick={() => revoke.mutate(item.id)}
                                    ><CloseIcon size={15} /></button>
                                )}
                            </li>
                        ))}
                    </ul>
                </>
            )}

            {door}
        </div>
    );
}
