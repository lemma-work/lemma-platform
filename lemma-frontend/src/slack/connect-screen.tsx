"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Screen } from "@/auth/screens";
import { source } from "@/data";
import type { SlackManagedWorkspace } from "@/data";
import { SessionGate, useSession } from "@/session/session";

/** Where "Connect your Lemma account" in Slack lands.
 *
 *  Lemma's app in Slack manages teammates, and managing needs to know who is
 *  asking for certain -- a Slack profile email is editable by a workspace admin,
 *  so it never stands in for an account. The link carries a signature only that
 *  person's own App Home could have shown them; signing in here and opening it
 *  is the proof, once. */
function readLink() {
    const query = new URLSearchParams(window.location.search);
    const expires = Number(query.get("exp"));
    const teamId = query.get("team") ?? "";
    const slackUserId = query.get("user") ?? "";
    const signature = query.get("sig") ?? "";
    if (!teamId || !slackUserId || !signature || !Number.isFinite(expires)) return null;
    return { teamId, slackUserId, expires, signature };
}

function backToSlack(teamId: string) {
    return `https://app.slack.com/client/${encodeURIComponent(teamId)}`;
}

function Linking() {
    const [link] = useState(readLink);
    const { user } = useSession();
    const preview = useQuery({
        queryKey: ["slack-person-preview", link?.teamId, link?.slackUserId],
        queryFn: () => {
            if (!link) throw new Error("This link is missing a part. Open Lemma's Home tab in Slack and connect again.");
            return source.previewSlackPerson(link);
        },
        retry: false,
    });
    const connect = useMutation({
        mutationFn: () => {
            if (!link) throw new Error("This link is missing a part.");
            return source.linkSlackPerson(link);
        },
    });

    if (preview.isError) {
        return <Screen title="That didn't connect" lead={preview.error.message} looking={false} />;
    }
    if (preview.isPending) {
        return <Screen title="Checking the link…" looking={false} />;
    }
    if (connect.isSuccess) {
        const workspace: SlackManagedWorkspace = connect.data;
        return (
            <Screen
                title="You're connected"
                lead={`Lemma knows it's you in ${workspace.teamName ?? "Slack"} now. Your teammates are on Lemma's Home tab there.`}
                looking={false}
            >
                <a className="btn btn--primary" href={backToSlack(workspace.teamId)}>Back to Slack</a>
            </Screen>
        );
    }
    const who = preview.data.slackUserName ?? "this Slack account";
    const where = preview.data.teamName ?? "Slack";
    /* Asked, never assumed: a link somebody else sent would otherwise connect
       their Slack account to yours, and Lemma would act on what they say. */
    return (
        <Screen
            title="Connect your Slack account?"
            lead={`${who} in ${where} will be you${user?.email ? ` (${user.email})` : ""} to Lemma's teammates there. Connect only if that Slack account is yours.`}
            looking={false}
        >
            {connect.isError && <p className="auth__problem" role="alert">{connect.error.message}</p>}
            <button className="btn btn--primary" disabled={connect.isPending} onClick={() => connect.mutate()}>
                {connect.isPending ? "Connecting…" : `Yes, I'm ${who}`}
            </button>
            <a className="linkish" href="/">That isn&rsquo;t me</a>
        </Screen>
    );
}

export function SlackConnectScreen() {
    return (
        <SessionGate>
            <Linking />
        </SessionGate>
    );
}
