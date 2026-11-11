"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { useSession } from "@/session/session";
import { safeDestination } from "@/auth/redirects";
import { POD_ROLES } from "@/data";
import { MATE } from "@/copy";
import { roleLabel } from "@/org/membership";

/** What a pod role lets you do, in the words the roster uses. */
function podCan(role: string | null | undefined): string | null {
    if (!role) return null;
    return POD_ROLES.find((entry) => entry.value === role)?.can ?? null;
}

/** An invitation, opened from its email.
 *
 *  Said as what it is for: "Join Support" when it brings you into a Lemma,
 *  "Join Acme" when it only brings you into the organization. The wire's
 *  enums (ORG_MEMBER, POD_USER) used to be printed as they came, under a
 *  "Workspace role" label that named neither thing.
 *
 *  Three ends that used to stop dead now say where to go: an invitation that
 *  was already accepted (by the arrival screen, or a second click) still
 *  offers the way in; a refusal names the account you are signed in with and
 *  offers another, because the commonest cause is the wrong one; and a
 *  declined one says so.
 */
export function InvitationDecision({
    id,
    decision,
}: {
    id: string;
    decision: "accept" | "reject";
}) {
    const session = useSession();
    const invitation = useQuery({
        queryKey: ["invitation", id],
        queryFn: () => lemma().organizations.invitations.get(id),
        retry: false,
    });
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        staleTime: 5 * 60_000,
    });
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [done, setDone] = useState(false);
    const [destination, setDestination] = useState<string | null>(null);

    const switchAccount = () => void session.leave(window.location.pathname + window.location.search);
    const signedInAs = me.data?.email ?? null;

    async function decide() {
        setBusy(true);
        setError("");
        try {
            if (decision === "accept") {
                const result = (await lemma().organizations.invitations.accept(id)) as { redirect_uri?: string };
                setDestination(safeDestination(result.redirect_uri || invitation.data?.redirect_uri || null) || "/home");
            } else await lemma().organizations.invitations.revoke(id);
            setDone(true);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not update the invitation.");
        } finally {
            setBusy(false);
        }
    }

    if (invitation.isPending) return <p role="status">Opening your invitation…</p>;
    if (invitation.isError)
        return (
            <section className="site-card">
                <h2>This invitation isn’t open to this account</h2>
                <p>
                    {signedInAs ? <>You’re signed in as {signedInAs}. </> : null}
                    Invitations open only for the address they were sent to. It may also have
                    expired or been withdrawn — if so, ask whoever invited you to send another.
                </p>
                <div className="site-actions">
                    <button className="btn btn--primary" onClick={switchAccount}>Use another account</button>
                    <button className="btn" onClick={() => void invitation.refetch()}>Try again</button>
                </div>
            </section>
        );

    const invite = invitation.data;
    const into = invite.pod_name || invite.organization_name || "Lemma";
    const org = invite.organization_name;
    const can = podCan(invite.pod_role);
    const fallback = safeDestination(invite.redirect_uri || null) || "/home";
    const accepted = done ? decision === "accept" : invite.status === "ACCEPTED";
    const declined = done ? decision === "reject" : invite.status === "REVOKED";
    const expired = !done && invite.status === "EXPIRED";

    return (
        <section className="site-card">
            <h2>
                {accepted ? "You’re in " + into : declined ? "Invitation declined" : expired ? "This invitation has expired" : "Join " + into}
            </h2>
            {invite.pod_name ? (
                <p>
                    {invite.pod_name} is a {MATE}{org ? " at " + org : ""}.
                    {invite.pod_description ? " " + invite.pod_description : ""}
                </p>
            ) : org ? (
                <p>An invitation to the {org} organization on Lemma.</p>
            ) : null}
            <dl>
                <dt>For</dt>
                <dd>{invite.email}</dd>
                {can && (
                    <>
                        <dt>In {invite.pod_name}</dt>
                        <dd>{can}</dd>
                    </>
                )}
                {org && (
                    <>
                        <dt>In {org}</dt>
                        <dd>{roleLabel(invite.role)}</dd>
                    </>
                )}
                {!accepted && !declined && (
                    <>
                        <dt>{expired ? "Expired" : "Open until"}</dt>
                        <dd>{new Date(invite.expires_at).toLocaleDateString()}</dd>
                    </>
                )}
            </dl>
            {accepted ? (
                <a className="btn btn--primary" href={destination ?? fallback}>
                    Open {into}
                </a>
            ) : declined ? (
                done ? <p>Nobody was added. You can close this tab.</p> : <p>It was withdrawn or turned down, so it can’t be used now.</p>
            ) : expired ? (
                <p>Ask whoever invited you to send a new one.</p>
            ) : (
                invite.status === "PENDING" && (
                    <button className={decision === "accept" ? "btn btn--primary" : "btn"} disabled={busy} onClick={() => void decide()}>
                        {busy ? "Updating…" : decision === "accept" ? "Accept and open " + into : "Decline invitation"}
                    </button>
                )
            )}
            {error && <p role="alert">{error}</p>}
        </section>
    );
}
