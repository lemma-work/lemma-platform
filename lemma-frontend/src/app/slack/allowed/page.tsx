"use client";

import { useEffect, useState } from "react";

/** Where Slack's Allow for a teammate's app lands, when it was started in Slack.
 *
 *  Nothing to do here, and nothing to sign in for: Lemma finishes adding the
 *  teammate on its own and says so in Slack. This page only has to say which
 *  way it went -- the callback names the outcome in the query string. */
export default function SlackAllowedPage() {
    const [refused, setRefused] = useState<string | null>(null);
    useEffect(() => {
        const query = new URLSearchParams(window.location.search);
        if (query.get("connect") === "error") {
            setRefused(query.get("reason") || "Slack didn't allow the app.");
        }
    }, []);
    return (
        <main className="portal">
            <div className="portal__pane">
                <div className="portal__form">
                    <h2>{refused ? "Not allowed" : "Allowed"}</h2>
                    <p className="portal__lead">
                        {refused
                            ? `${refused} Go back to Slack and try "Add to Slack" again.`
                            : "Your teammate is joining Slack. Lemma will tell you there when it’s in. You can close this tab."}
                    </p>
                </div>
            </div>
        </main>
    );
}
