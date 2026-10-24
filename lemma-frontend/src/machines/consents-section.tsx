"use client";

import { agoOf } from "@/schedule/schedules";
import { live } from "@/usage/queries";
import { useMachineConsents, useRevokeMachineConsent } from "./consent";

/** Settings → Apps on your machine: every app somebody let run commands as
 *  them, each with the way to take it back.
 *
 *  Revoking is one click with no confirmation, like disconnecting an AI tool:
 *  it is undone by the app asking again, and the person is right here. */
export function MachineConsentsSection() {
    const consents = useMachineConsents();
    const revoke = useRevokeMachineConsent();

    /* Sample mode asks nothing, so it has nothing allowed. */
    const allowed = live() ? consents.data : [];
    if (!allowed && consents.isPending) return <p role="status">Loading the apps you have allowed…</p>;
    if (consents.isError) {
        return (
            <div className="access__head">
                <small role="alert">The apps you have allowed could not be loaded.</small>
                <button className="access__copy" onClick={() => void consents.refetch()}>Try again</button>
            </div>
        );
    }
    if (!allowed || allowed.length === 0) {
        return (
            <div className="access__head">
                <small>No app can use your machine. An app that needs to will ask you first.</small>
            </div>
        );
    }
    return (
        <>
            {revoke.isError && (
                <div className="access__head">
                    <small role="alert">{revoke.error instanceof Error ? revoke.error.message : "That did not work."}</small>
                </div>
            )}
            <ul className="access__prompts">
                {allowed.map((consent) => (
                    <li key={consent.app_id}>
                        <span>
                            <b>{consent.app_name ?? "A deleted app"}</b>
                            <small>Allowed {agoOf(consent.granted_at)}</small>
                        </span>
                        <span className="access__actions">
                            <button
                                className="access__copy"
                                disabled={revoke.isPending && revoke.variables === consent.app_id}
                                onClick={() => revoke.mutate(consent.app_id)}
                            >
                                Revoke
                            </button>
                        </span>
                    </li>
                ))}
            </ul>
        </>
    );
}
