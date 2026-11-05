"use client";

import { useEffect, useState } from "react";
import { useConnectOutcome, type ConnectOutcome } from "@/connect/round-trip";
import { installedNote } from "@/slack/install";

/** Where Slack's consent for Lemma's own app lands, after "Add to Slack".
 *
 *  Nothing is left to do here: the workspace is adopted when the grant comes
 *  back, and Lemma says hello in Slack -- or, if it can't stay, says why
 *  there. This page only says which way the round trip went, read once from
 *  the query string and stripped (so a reload is not a second outcome), in
 *  fixed words: what a link carries is never shown back. */
export default function SlackInstalledPage() {
    const [outcome, setOutcome] = useState<ConnectOutcome | null>(null);
    useConnectOutcome(setOutcome, () => {});
    /* After the hook's own effect has read the query: until then there is no
       outcome to speak of, rather than "nothing to finish". */
    const [read, setRead] = useState(false);
    useEffect(() => setRead(true), []);
    if (!read) return <main className="portal" />;
    const note = installedNote(outcome);
    return (
        <main className="portal">
            <div className="portal__pane">
                <div className="portal__form">
                    <h2>{note.title}</h2>
                    <p className="portal__lead">{note.lead}</p>
                    {note.ok
                        ? <a className="btn btn--primary" href="https://app.slack.com/client">Open Slack</a>
                        : <a className="btn" href="/slack/install">Add to Slack</a>}
                </div>
            </div>
        </main>
    );
}
