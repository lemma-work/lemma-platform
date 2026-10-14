"use client";

import { useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { Screen } from "@/auth/screens";
import { source } from "@/data";
import { SessionGate } from "@/session/session";

/** "Add to Slack", from outside the app.
 *
 *  Signing in first is the point, not a hurdle: Lemma's Slack app manages a
 *  workspace's teammates for an organization, so there has to be a person and
 *  an organization before Slack is asked. Lemma's ordinary sign-in is used, so
 *  an email Slack vouches for never opens an account somebody else made. Then
 *  the browser goes to Slack's consent, and Slack is where it ends. */
function Installing() {
    const start = useMutation({
        mutationFn: () => source.startSlackInstall(),
        onSuccess: (url) => window.location.assign(url),
    });
    useEffect(() => {
        if (start.isIdle) start.mutate();
    }, [start]);

    if (start.isError) {
        return (
            <Screen title="Lemma couldn't start the Slack install" lead={start.error.message} looking={false}>
                <button className="btn" onClick={() => start.mutate()}>Try again</button>
            </Screen>
        );
    }
    return <Screen title="Taking you to Slack…" lead="Slack asks whether Lemma may join your workspace." looking={false} />;
}

export function SlackInstallScreen() {
    return (
        <SessionGate>
            <Installing />
        </SessionGate>
    );
}
