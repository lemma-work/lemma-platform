"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Screen } from "@/auth/screens";
import { source } from "@/data";
import { SessionGate } from "@/session/session";
import { installStep, once } from "./install";

/** "Add to Slack", from outside the app.
 *
 *  Signing in first is the point, not a hurdle: Lemma's Slack app manages a
 *  workspace's teammates for an organization, so there has to be a person and
 *  an organization before Slack is asked. Lemma's ordinary sign-in is used, so
 *  an email Slack vouches for never opens an account somebody else made.
 *
 *  Nothing happens on arrival. A link that lands somebody here signed in does
 *  not make them an organization or start a grant: the click does, once, and
 *  only after the server said Slack is set up here. Slack's Allow has to be
 *  finished in this same browser, signed in as this same person, or nothing
 *  is joined. */
function Installing() {
    const available = useQuery({ queryKey: ["slack-install-available"], queryFn: () => source.slackInstallAvailable() });
    const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => source.listOrgs() });
    const step = installStep(available.data, orgs.data);
    const [chosen, setChosen] = useState("");
    const orgId = chosen || (step.kind === "choose" ? step.orgs[0].id : "");

    const startOnce = useMemo(
        () =>
            once(async (forOrg: string) => {
                const organization = forOrg || (await source.ensureFirstWorkspace()).orgId;
                return source.startSlackInstall(organization);
            }),
        [],
    );
    const start = useMutation({
        mutationFn: startOnce,
        onSuccess: (url) => window.location.assign(url),
        /* A newcomer may have been given an organization before the refusal
           -- their company's, joined by email domain, where they only
           belong: show it, and any other they could pick. */
        onError: () => void orgs.refetch(),
    });
    const going = start.isPending || start.isSuccess;

    if (available.isError || orgs.isError) {
        return (
            <Screen title="Lemma couldn't get ready for Slack" lead="That didn't load. Try again in a moment." looking={false}>
                <button className="btn" onClick={() => { void available.refetch(); void orgs.refetch(); }}>Try again</button>
            </Screen>
        );
    }
    if (step.kind === "loading") return <Screen title="Add Lemma to Slack" lead="One moment…" looking={false} />;
    if (step.kind === "unavailable") {
        return (
            <Screen
                title="Slack isn't set up here"
                lead="This Lemma server has no Slack app of its own yet. Whoever runs it can add one; nothing was changed."
                looking={false}
            />
        );
    }

    const problem = start.isError && <p className="auth__problem" role="alert">{start.error.message}</p>;
    const button = (
        <button className="btn btn--primary" disabled={going} onClick={() => start.mutate(orgId)}>
            {going ? "Opening Slack…" : step.kind === "first" ? "Set up Lemma and add to Slack" : "Add to Slack"}
        </button>
    );
    if (step.kind === "first") {
        return (
            <Screen
                title="Add Lemma to Slack"
                lead="Lemma sets you up first — an organization and a first teammate — then Slack asks whether Lemma may join your workspace."
                looking={false}
            >
                {problem}
                {button}
            </Screen>
        );
    }
    const name = step.orgs.find((org) => org.id === orgId)?.name ?? step.orgs[0].name;
    return (
        <Screen
            title="Add Lemma to Slack"
            lead={`Lemma joins your Slack workspace for ${name}, and sets your teammates up there. Slack asks you to allow it next.`}
            looking={false}
        >
            {step.picker && (
                <div className="field">
                    <label htmlFor="slack-install-org">Organization</label>
                    <select id="slack-install-org" value={orgId} disabled={going} onChange={(event) => setChosen(event.target.value)}>
                        {step.orgs.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
                    </select>
                </div>
            )}
            {problem}
            {button}
        </Screen>
    );
}

export function SlackInstallScreen() {
    return (
        <SessionGate>
            <Installing />
        </SessionGate>
    );
}
