import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import type { Pod } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import { canManage, inviteProblem, unsentInvitation, type InvitationForInviter } from "@/org/membership";
import { CopyLink } from "@/org/people";

const ROLES = [
    { value: "POD_EDITOR", label: "Can edit" },
    { value: "POD_USER", label: "Can use" },
    { value: "POD_VIEWER", label: "Can read" },
    { value: "POD_ADMIN", label: "Admin" },
];

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

/** One field for both ways somebody gets into a pod.
 *
 *  Somebody already in the organization is added straight away —
 *  `podMembers.add` takes an `organization_member_id`. Somebody who is not is
 *  sent an organization invitation that names this pod, its role and this
 *  pod's address, so accepting it joins both and lands them here. Which of the
 *  two happened is ours to know; the person typing only knows an address.
 *
 *  Only an organization owner or editor may invite, and the server refuses
 *  anybody else. For them the field stays a search over the organization, and
 *  says who to ask, rather than offering a button that always fails. */
export function AddPeople({ pod, orgId, onDone }: { pod: Pod; orgId: string | null; onDone: () => void }) {
    const [role, setRole] = useState(ROLES[0].value);
    const [query, setQuery] = useState("");
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [invited, setInvited] = useState<string | null>(null);
    const [unsent, setUnsent] = useState<ReturnType<typeof unsentInvitation>>(null);
    const queryClient = useQueryClient();
    const preview = isLandingPreview();

    const orgMembers = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: async () => {
            if (preview) {
                const { previewCandidates } = await import("@/marketing/preview-source");
                return { items: previewCandidates.map(person => ({ id: person.id, role: person.orgRole, user: { email: person.label } })) };
            }
            return lemma().organizations.members.list(orgId as string, { limit: 100 });
        },
        enabled: Boolean(orgId),
    });

    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: !preview,
        staleTime: 5 * 60_000,
    });

    /* My own role is only knowable by finding myself in the members list, the
       way the People settings find it. Unknown counts as no: nothing is offered
       on a guess. */
    const myRole = useMemo(() => {
        const mine = itemsOf(orgMembers.data)
            .map((raw) => raw as { user_id?: string; role?: string })
            .find((member) => member.user_id && member.user_id === me.data?.id);
        return mine?.role ?? null;
    }, [orgMembers.data, me.data?.id]);
    const mayInvite = !preview && canManage(myRole);

    const invitations = useQuery({
        queryKey: ["org-invitations", orgId],
        queryFn: () => lemma().organizations.invitations.list(orgId as string, { limit: 50 }),
        enabled: mayInvite && Boolean(orgId),
    });

    /* Invitations already waiting on this pod, shown in the list so nobody
       invites the same address twice wondering whether the first one went. */
    const pendingHere = useMemo(
        () =>
            (itemsOf(invitations.data) as PodInvite[]).filter(
                (invite) => invite.status === "PENDING" && invite.pod_id === pod.id,
            ),
        [invitations.data, pod.id],
    );

    const alreadyIn = useMemo(
        () => new Set(pod.members.map((member) => member.name.toLowerCase())),
        [pod.members],
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
        : looksLikeEmail && mayInvite && !inPod && !pendingMatch
          ? "invite"
          : null;

    /* Said under the field, before anything is pressed, so the answer to "why
       is the button off" is already on screen. */
    const hint = inPod
        ? "They’re already in this pod."
        : pendingMatch
          ? "They’ve already been invited here."
          : looksLikeEmail && !exact && !mayInvite && !preview
            ? "They aren’t in this organization yet. Ask an organization owner or editor to invite them."
            : null;

    async function add(memberId: string) {
        setBusy(memberId);
        setError(null);
        try {
            if (preview) {
                const { addPreviewMember } = await import("@/marketing/preview-source");
                addPreviewMember(pod.id, memberId, ROLES.find(option => option.value === role)?.label ?? role);
                await queryClient.invalidateQueries({ queryKey: ["pod-detail", pod.id] });
            } else {
                await lemma(pod.id).podMembers.add(pod.id, {
                    organization_member_id: memberId,
                    roles: [role],
                });
            }
            await queryClient.invalidateQueries({ queryKey: ["pods"] });
            onDone();
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
                    aria-label={mayInvite ? "Email address or name" : "Search this organization"}
                    placeholder={mayInvite ? "Add by email" : "Search this organization"}
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

            {orgMembers.isPending && <p className="empty-row">Reading your organization…</p>}
            {orgMembers.isError && <p className="empty-row">Couldn’t load organization members.</p>}
            {orgMembers.isSuccess && !typed && candidates.length === 0 && pendingHere.length === 0 && (
                <p className="empty-row">
                    {mayInvite ? "Everyone in this organization is already here. Add someone new by email." : "Everyone in this organization is already here."}
                </p>
            )}

            <div className="addpeople__list">
                {shown.map((candidate) => (
                    <button
                        key={candidate.id}
                        type="button"
                        className="addpeople__row"
                        disabled={busy !== null}
                        onClick={() => void add(candidate.id)}
                    >
                        <span className="addpeople__name">{candidate.label}</span>
                        <span className="addpeople__org">{candidate.orgRole}</span>
                        <span className="addpeople__go">{busy === candidate.id ? "Adding…" : "Add"}</span>
                    </button>
                ))}
                {!typed &&
                    pendingHere.map((invite) => (
                        <div key={invite.id} className="addpeople__row addpeople__row--pending">
                            <span className="addpeople__name">{invite.email}</span>
                            <span className="addpeople__org">invited</span>
                        </div>
                    ))}
            </div>
        </div>
    );
}
