"use client";

import { useEffect, useState } from "react";

/** Where Slack's consent for Lemma's own app lands, after "Add to Slack".
 *
 *  Nothing is left to do here: the workspace is adopted when the grant comes
 *  back, and Lemma says hello in Slack. This page only says which way it went
 *  -- the callback names the outcome in the query string. */
export default function SlackInstalledPage() {
    const [refused, setRefused] = useState<string | null>(null);
    useEffect(() => {
        const query = new URLSearchParams(window.location.search);
        if (query.get("connect") === "error") setRefused(query.get("reason") || "Slack didn't add Lemma.");
    }, []);
    return (
        <main className="portal">
            <div className="portal__pane">
                <div className="portal__form">
                    <h2>{refused ? "Lemma isn't in Slack yet" : "Slack added Lemma"}</h2>
                    {refused ? (
                        /* Slack's own words, then ours: two sentences, never one run together. */
                        <p className="portal__lead">{refused.replace(/[.\s]+$/, "")}. Try &ldquo;Add to Slack&rdquo; again.</p>
                    ) : (
                        <p className="portal__lead">
                            Open Lemma&rsquo;s Home tab in Slack to add your first teammate. If the workspace
                            already belongs to another organization on Lemma, Lemma says so in Slack when it can.
                        </p>
                    )}
                    {!refused && <a className="btn btn--primary" href="https://app.slack.com/client">Open Slack</a>}
                </div>
            </div>
        </main>
    );
}
