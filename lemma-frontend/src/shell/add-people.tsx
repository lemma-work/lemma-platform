import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { MANAGE_MEMBERS, POD_ROLES, type Pod } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import { inviteProblem, unsentInvitation, type InvitationForInviter } from "@/org/membership";
import { CopyLink } from "@/org/people";

/** The picker's order: the common grant first, the one that hands over the
 *  membership itself last. */
const ROLES = [...POD_ROLES.slice(1), POD_ROLES[0]];

interface Listish {
    items?: unknown[];
}

interface PodInvite extends InvitationForInviter {
    id: string;
    status?: string | null;
    pod_id?: string | null;
}

function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    return (value as Listish)?.items ?? [];
}

/** Whether the server lets the signed-in person add people to this pod.
 *
 *  Asked of `podPermissions.me` — whether this person holds
 *  `pod.member.manage` here — rather than guessed from a role. `allowed` is
 *  false until the server has said yes; `answered` is whether it has said
 *  anything, so a "you can't" line is not drawn while the question is in
 *  flight. One query key, so the dialog and the profile ask once between
 *  them. */
export function useCanManageMembers(podId: string): { allowed: boolean; answered: boolean } {
    const preview = isLandingPreview();
    const permissions = useQuery({
        queryKey: ["pod-permissions", podId],
        queryFn: () => lemma(podId).podPermissions.me(podId),
        enabled: !preview,
        staleTime: 60_000,
    });
    if (preview) return { allowed: true, answered: true };
    return {
        allowed: (permissions.data?.actions ?? []).includes(MANAGE_MEMBERS),
        answered: permissions.isSuccess,
    };
}

/** Who has access to a pod, and — for whoever may change that — the way to
 *  add more.
 *
 *  The list comes first because it is the question the header's faces ask.
 *  It is readable by anybody in the pod, and an empty dialog that only
 *  offered to add people answered "who has access" with nothing.
 *
 *  "With access", not "in": the pod is a teammate, and people are not in a
 *  teammate. It is also what the profile calls the same list.
 *
 *  Adding is one field for both ways somebody gets into a pod. Somebody already
 *  in the organization is added straight away — `podMembers.add` takes an
 *  `organization_member_id`. Somebody who is not is sent an organization
 *  invitation that names this pod, its role and this pod's address, so
 *  accepting it joins both and lands them here.
 *
 *  Whether the field is offered at all is `useCanManageMembers`. Unknown
 *  counts as no — nothing is offered that the server has not said it will
 *  allow. */
export function AddPeople({ pod, orgId }: { pod: Pod; orgId: string | null }) {
    const [role, setRole] = useState<string>(ROLES[0].value);
    const [query, setQuery] = useState("");
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [invited, setInvited] = useState<string | null>(null);
    const [unsent, setUnsent] = useState<ReturnType<typeof unsentInvitation>>(null);
    const queryClient = useQueryClient();
    const preview = isLandingPreview();

    const permissions = useCanManageMembers(pod.id);
    const canAdd = permissions.allowed;

    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: !preview,
        staleTime: 5 * 60_000,
    });
    const myEmail = (me.data?.email ?? "").toLowerCase();

    const orgMembers = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: async () => {
            if (preview) {
                const { previewCandidates } = await import("@/marketing/preview-source");
                return { items: previewCandidates.map(person => ({ id: person.id, role: person.orgRole, user: { email: person.label } })) };
            }
            return lemma().organizations.members.list(orgId as string, { limit: 100 });
        },
        enabled: canAdd && Boolean(orgId),
    });

    const invitations = useQuery({
        queryKey: ["org-invitations", orgId],
        queryFn: () => lemma().organizations.invitations.list(orgId as string, { limit: 50 }),
        enabled: canAdd && !preview && Boolean(orgId),
    });

    /* Invitations already waiting on this pod, listed with the people here so
       nobody invites the same address twice wondering whether the first one
       went. */
    const pendingHere = useMemo(
        () =>
            (itemsOf(invitations.data) as PodInvite[]).filter(
                (invite) => invite.status === "PENDING" && invite.pod_id === pod.id,
            ),
        [invitations.data, pod.id],
    );

    const people = useMemo(() => pod.members.filter((member) => member.kind === "person"), [pod.members]);

    /* By address, which is what an organization member is known by here. The
       name is the fallback only for a source that has no address to give. */
    const alreadyIn = useMemo(
        () => new Set(people.map((member) => (member.email ?? member.name).toLowerCase())),
        [people],
    );

    const candidates = useMemo(() => {
        return itemsOf(orgMembers.data)
            .map((raw) => raw as { id: string; role?: string; user?: { email?: string; name?: string } | null })
            .map((member) => ({
                id: member.id,
                label: member.user?.email ?? member.user?.name ?? "Member",
                orgRole: (member.role ?? "").replace("ORG_", "").toLowerCase(),
            }))
            .filter((member) => !alreadyIn.has(member.label.toLowerCase()));
    }, [orgMembers.data, alreadyIn]);

    const typed = query.trim().toLowerCase();
    const shown = typed ? candidates.filter((candidate) => candidate.label.toLowerCase().includes(typed)) : candidates;
    const exact = candidates.find((candidate) => candidate.label.toLowerCase() === typed) ?? null;
    const pendingMatch = pendingHere.some((invite) => (invite.email ?? "").toLowerCase() === typed);
    const inPod = typed !== "" && alreadyIn.has(typed);
    const looksLikeEmail = typed !== "" && inviteProblem(typed) === null;

    /* What pressing Enter would do, decided once so the button and the key
       agree. */
    const action: "add" | "invite" | null = exact
        ? "add"
        : looksLikeEmail && !preview && !inPod && !pendingMatch
          ? "invite"
          : null;

    /* Said under the field, before anything is pressed, so the answer to "why
       is the button off" is already on screen. */
    const hint = inPod
        ? "They’re already in this pod."
        : pendingMatch
          ? "They’ve already been invited here."
          : !typed && orgMembers.isSuccess && candidates.length === 0 && pendingHere.length === 0
            ? "Everyone in this organization is already here. Add someone new by email."
            : null;

    async function add(memberId: string) {
        setBusy(memberId);
        setError(null);
        try {
            if (preview) {
                const { addPreviewMember } = await import("@/marketing/preview-source");
                addPreviewMember(pod.id, memberId, ROLES.find(option => option.value === role)?.label ?? role);
            } else {
                await lemma(pod.id).podMembers.add(pod.id, {
                    organization_member_id: memberId,
                    roles: [role],
                });
            }
            setQuery("");
            /* Stays open: the list is right here, and the person just added
               appearing in it is the confirmation. */
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ["pod-detail", pod.id] }),
                queryClient.invalidateQueries({ queryKey: ["pods"] }),
            ]);
        } catch (problem) {
            setError(problem instanceof Error ? problem.message : "Couldn’t add this person.");
        } finally {
            setBusy(null);
        }
    }

    async function invite(email: string) {
        if (!orgId) return;
        setBusy("invite");
        setError(null);
        setInvited(null);
        setUnsent(null);
        try {
            const created = (await lemma().organizations.invitations.invite(orgId, {
                email,
                /* The least the organization can give. The pod role is what
                   they were asked for. */
                role: "ORG_MEMBER" as never,
                pod_id: pod.id,
                pod_role: role,
                /* Relative, so it resolves against whichever address they open
                   the invitation on; the accept page only honours a safe one. */
                redirect_uri: "/t/" + encodeURIComponent(pod.id),
            })) as PodInvite;
            setQuery("");
            setInvited(email);
            setUnsent(unsentInvitation(created));
            await queryClient.invalidateQueries({ queryKey: ["org-invitations", orgId] });
        } catch (problem) {
            setError(problem instanceof Error ? problem.message : "That invitation was not sent.");
        } finally {
            setBusy(null);
        }
    }

    function submit() {
        if (busy !== null) return;
        if (action === "add" && exact) void add(exact.id);
        else if (action === "invite") void invite(query.trim());
        else if (typed && !looksLikeEmail && shown.length === 0) setError(inviteProblem(query));
    }

    return (
        <div className="addpeople addpeople--modal">
            {canAdd && (
                <form
                    className="addpeople__bar"
                    onSubmit={(event) => {
                        event.preventDefault();
                        submit();
                    }}
                >
                    <input
                        className="addpeople__email"
                        type="text"
                        inputMode="email"
                        autoComplete="off"
                        autoFocus
                        aria-label="Email address or name"
                        placeholder="Add by email"
                        value={query}
                        onChange={(event) => {
                            setQuery(event.target.value);
                            setError(null);
                        }}
                    />
                    <select aria-label="Access level" className="addpeople__role" value={role} onChange={(event) => setRole(event.target.value)}>
                        {ROLES.map((option) => (
                            <option key={option.value} value={option.value}>
                                {option.label}
                            </option>
                        ))}
                    </select>
                    <button className="btn btn--primary" type="submit" disabled={busy !== null || action === null}>
                        {busy === "invite" ? "Sending…" : action === "invite" ? "Invite" : "Add"}
                    </button>
                </form>
            )}

            {(error || hint) && (
                <p className="addpeople__hint" role={error ? "alert" : undefined} data-bad={Boolean(error)}>
                    {error ?? hint}
                </p>
            )}
            {invited && !unsent && (
                <p className="addpeople__hint" role="status">
                    Invited {invited}. They’ll land in {pod.name} once they accept.
                </p>
            )}
            {unsent && (
                <div className="addpeople__unsent" role="status">
                    <p className="addpeople__hint">{unsent.said}</p>
                    {unsent.link && (
                        <div className="addpeople__bar">
                            <input
                                className="addpeople__email"
                                readOnly
                                value={unsent.link}
                                aria-label={"Invitation link for " + unsent.to}
                                onFocus={(event) => event.target.select()}
                            />
                            <CopyLink link={unsent.link} />
                        </div>
                    )}
                </div>
            )}

            {typed ? (
                /* Typing is a search over who could be added, so the answer
                   replaces the list rather than being buried under it. */
                <div className="addpeople__list">
                    {shown.map((candidate) => (
                        <CandidateRow key={candidate.id} candidate={candidate} busy={busy} onAdd={add} />
                    ))}
                </div>
            ) : (
                <>
                    <div className="addpeople__list" aria-label={"People with access to " + pod.name}>
                        {people.map((member) => {
                            const mine = Boolean(myEmail) && (member.email ?? "").toLowerCase() === myEmail;
                            return (
                                <div key={member.id} className="addpeople__row addpeople__row--member" title={member.email}>
                                    <span className="face" aria-hidden="true">{member.initials}</span>
                                    <span className="addpeople__name">
                                        {member.name}
                                        {mine && <span className="addpeople__you"> · you</span>}
                                    </span>
                                    <span className="addpeople__access">{member.can}</span>
                                </div>
                            );
                        })}
                        {pendingHere.map((invite) => (
                            <div key={invite.id} className="addpeople__row addpeople__row--member addpeople__row--pending">
                                <span className="face" aria-hidden="true">@</span>
                                <span className="addpeople__name">{invite.email}</span>
                                <span className="addpeople__access">Invited</span>
                            </div>
                        ))}
                    </div>

                    {canAdd && orgMembers.isPending && <p className="empty-row">Reading your organization…</p>}
                    {canAdd && orgMembers.isError && <p className="empty-row">Couldn’t load organization members.</p>}
                    {canAdd && candidates.length > 0 && (
                        <>
                            <p className="addpeople__section">Also in this organization</p>
                            <div className="addpeople__list">
                                {candidates.map((candidate) => (
                                    <CandidateRow key={candidate.id} candidate={candidate} busy={busy} onAdd={add} />
                                ))}
                            </div>
                        </>
                    )}
                    {permissions.answered && !canAdd && (
                        <p className="addpeople__hint">Only admins of {pod.name} can add people.</p>
                    )}
                </>
            )}
        </div>
    );
}

/** The way into the dialog from anywhere but the header — the profile's
 *  roster. Nothing at all for somebody the server will not let add, for the
 *  reason the dialog hides its field. */
export function AddPeopleButton({ podId, onOpen }: { podId: string; onOpen: () => void }) {
    const { allowed } = useCanManageMembers(podId);
    if (!allowed) return null;
    return (
        <button type="button" className="btn btn--small" onClick={onOpen}>
            Add people
        </button>
    );
}

function CandidateRow({ candidate, busy, onAdd }: {
    candidate: { id: string; label: string; orgRole: string };
    busy: string | null;
    onAdd: (memberId: string) => void;
}) {
    return (
        <button
            type="button"
            className="addpeople__row"
            disabled={busy !== null}
            onClick={() => onAdd(candidate.id)}
        >
            <span className="addpeople__name">{candidate.label}</span>
            <span className="addpeople__org">{candidate.orgRole}</span>
            <span className="addpeople__go">{busy === candidate.id ? "Adding…" : "Add"}</span>
        </button>
    );
}
