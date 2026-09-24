"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { safeDestination } from "@/auth/redirects";
export function InvitationDecision({
    id,
    decision,
}: {
    id: string;
    decision: "accept" | "reject";
}) {
    const invitation = useQuery({
        queryKey: ["invitation", id],
        queryFn: () => lemma().organizations.invitations.get(id),
    });
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [done, setDone] = useState(false);
    const [destination, setDestination] = useState("/t");
    async function decide() {
        setBusy(true);
        setError("");
        try {
            if (decision === "accept") {
                const result = (await lemma().organizations.invitations.accept(
                    id,
                )) as { redirect_uri?: string };
                setDestination(
                    safeDestination(
                        result.redirect_uri ||
                            invitation.data?.redirect_uri ||
                            null,
                    ) || "/t",
                );
            } else await lemma().organizations.invitations.revoke(id);
            setDone(true);
        } catch (e) {
            setError(
                e instanceof Error
                    ? e.message
                    : "Could not update the invitation.",
            );
        } finally {
            setBusy(false);
        }
    }
    if (invitation.isPending) return <p role="status">Loading invitation…</p>;
    if (invitation.isError)
        return (
            <section>
                <p role="alert">
                    This invitation is not available to your account. It may
                    have expired or been revoked.
                </p>
                <button
                    className="btn"
                    onClick={() => void invitation.refetch()}
                >
                    Retry
                </button>
            </section>
        );
    const invite = invitation.data;
    return (
        <section className="site-card">
            <h2>{invite.pod_name || "Organization invitation"}</h2>
            <p>{invite.pod_description}</p>
            <dl>
                <dt>Invited account</dt>
                <dd>{invite.email}</dd>
                <dt>Organization role</dt>
                <dd>{invite.role}</dd>
                {invite.pod_role && (
                    <>
                        <dt>Workspace role</dt>
                        <dd>{invite.pod_role}</dd>
                    </>
                )}
                <dt>Status</dt>
                <dd>
                    {done
                        ? decision === "accept"
                            ? "Accepted"
                            : "Declined"
                        : invite.status}
                </dd>
                <dt>Expires</dt>
                <dd>{new Date(invite.expires_at).toLocaleDateString()}</dd>
            </dl>
            {done ? (
                <a className="btn btn--primary" href={destination}>
                    Continue ↗
                </a>
            ) : (
                invite.status === "PENDING" && (
                    <button
                        className="btn btn--primary"
                        disabled={busy}
                        onClick={() => void decide()}
                    >
                        {busy
                            ? "Updating…"
                            : decision === "accept"
                              ? "Accept invitation"
                              : "Decline invitation"}
                    </button>
                )
            )}
            {error && <p role="alert">{error}</p>}
        </section>
    );
}
