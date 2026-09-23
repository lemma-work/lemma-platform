import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import type { Pod } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";

const ROLES = [
    { value: "POD_EDITOR", label: "Can edit" },
    { value: "POD_USER", label: "Can use" },
    { value: "POD_VIEWER", label: "Can read" },
    { value: "POD_ADMIN", label: "Admin" },
];

interface Listish {
    items?: unknown[];
}

function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    return (value as Listish)?.items ?? [];
}

/** Adding a person means adding somebody who is already in the organization —
 *  `podMembers.add` takes an `organization_member_id`, not an email. Inviting
 *  a stranger is an org-level act and lives in settings. */
export function AddPeople({ pod, orgId, onDone }: { pod: Pod; orgId: string | null; onDone: () => void }) {
    const [role, setRole] = useState(ROLES[0].value);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const queryClient = useQueryClient();

    const orgMembers = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: async () => {
            if (isLandingPreview()) {
                const { previewCandidates } = await import("@/marketing/preview-source");
                return { items: previewCandidates.map(person => ({ id: person.id, role: person.orgRole, user: { email: person.label } })) };
            }
            return lemma().organizations.members.list(orgId as string, { limit: 100 });
        },
        enabled: Boolean(orgId),
    });

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

    async function add(memberId: string) {
        setBusy(memberId);
        setError(null);
        try {
            if (isLandingPreview()) {
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

    return (
        <div className="addpeople addpeople--modal">
            <div className="addpeople__head">
                <span className="addpeople__rolelabel">They can</span>
                <select aria-label="Access level" className="addpeople__role" value={role} onChange={(event) => setRole(event.target.value)}>
                    {ROLES.map((option) => (
                        <option key={option.value} value={option.value}>
                            {option.label}
                        </option>
                    ))}
                </select>
            </div>

            {error && <p className="addpeople__error">{error}</p>}
            {orgMembers.isPending && <p className="empty-row">Reading your organization…</p>}
            {orgMembers.isError && <p className="empty-row">Couldn’t load organization members.</p>}
            {orgMembers.isSuccess && candidates.length === 0 && (
                <p className="empty-row">Everyone in this organization is already here.</p>
            )}

            <div className="addpeople__list">
                {candidates.map((candidate) => (
                    <button
                        key={candidate.id}
                        className="addpeople__row"
                        disabled={busy !== null}
                        onClick={() => void add(candidate.id)}
                    >
                        <span className="addpeople__name">{candidate.label}</span>
                        <span className="addpeople__org">{candidate.orgRole}</span>
                        <span className="addpeople__go">{busy === candidate.id ? "Adding…" : "Add"}</span>
                    </button>
                ))}
            </div>
        </div>
    );
}
