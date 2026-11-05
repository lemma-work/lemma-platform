"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Screen } from "@/auth/screens";
import { source } from "@/data";
import type { SlackPersonLink } from "@/data";
import { SessionGate, useSession } from "@/session/session";

/** Where "Connect your Lemma account" in Slack lands, and where Sign in with
 *  Slack comes back to.
 *
 *  Lemma's app in Slack manages teammates, and managing needs to know who is
 *  asking for certain -- a Slack profile email is editable by a workspace
 *  admin, so it never stands in for an account. The link only says which Slack
 *  user it was shown to; the proof is signing in with Slack here, as that same
 *  person, from this same Lemma session. A link somebody else sent you proves
 *  nothing: you would sign in to Slack as yourself, and Lemma refuses. */
function readPage(): { link: SlackPersonLink | null; connected: boolean; problem: string | null } {
    const query = new URLSearchParams(window.location.search);
    const teamId = query.get("team") ?? "";
    const slackUserId = query.get("user") ?? "";
    const error = query.get("error");
    // Read once; a reload is not a second outcome.
    if (query.has("connected") || error) window.history.replaceState(null, "", window.location.pathname);
    return {
        link: teamId && slackUserId ? { teamId, slackUserId } : null,
        connected: query.get("connected") === "1",
        problem: error ? REFUSALS[error] ?? REFUSALS.unavailable : null,
    };
}

/** What each of the server's fixed refusal codes means, in words; nothing a
 *  link carried is ever shown back. */
const REFUSALS: Record<string, string> = {
    expired: "That sign-in took too long or was already used. Open Lemma's Home tab in Slack and connect again.",
    session_changed: "Finish signing in from the browser you started in, signed in to Lemma as yourself.",
    other_slack_user: "You signed in to Slack as someone other than the person this link was for. Open Lemma's Home tab in Slack yourself, and connect from there.",
    not_in_organization: "That Slack workspace belongs to a Lemma organization you're not in. Ask an owner to invite you, then connect again.",
    not_installed: "Lemma isn't installed in that Slack workspace any more.",
    cancelled: "Sign in with Slack was cancelled. Nothing was connected.",
    unavailable: "Sign in with Slack didn't work just now. Try again shortly.",
};

function Connecting() {
    const [page] = useState(readPage);
    const { user } = useSession();
    const signIn = useMutation({
        mutationFn: () => {
            if (!page.link) throw new Error("This link is missing a part. Open Lemma's Home tab in Slack and connect again.");
            return source.startSlackSignIn(page.link);
        },
        onSuccess: (url) => window.location.assign(url),
    });

    if (page.connected) {
        return (
            <Screen
                title="You're connected"
                lead="Lemma knows it's you in Slack now. Your teammates are on Lemma's Home tab there."
                looking={false}
            >
                <a className="btn btn--primary" href="https://app.slack.com/client">Back to Slack</a>
            </Screen>
        );
    }
    if (page.problem) {
        return <Screen title="That didn't connect" lead={page.problem} looking={false} />;
    }
    if (!page.link) {
        return <Screen title="That didn't connect" lead="This link is missing a part. Open Lemma's Home tab in Slack and connect again." looking={false} />;
    }
    return (
        <Screen
            title="Connect your Slack account"
            lead={`Sign in with Slack to show Lemma that this Slack account is yours${user?.email ? ` (${user.email})` : ""}. Slack says who you are; nothing else is asked for.`}
            looking={false}
        >
            {signIn.isError && <p className="auth__problem" role="alert">{signIn.error.message}</p>}
            <button className="btn btn--primary" disabled={signIn.isPending || signIn.isSuccess} onClick={() => signIn.mutate()}>
                {signIn.isPending || signIn.isSuccess ? "Opening Slack…" : "Sign in with Slack"}
            </button>
        </Screen>
    );
}

export function SlackConnectScreen() {
    return (
        <SessionGate>
            <Connecting />
        </SessionGate>
    );
}
