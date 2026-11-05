"use client";

import type { Org } from "@/data";
import { ConnectorsSection } from "@/org/connectors";

/** The services the organization's Lemmas can work through -- Gmail,
 *  GitHub, Slack, a database -- as a place of its own in the sidebar, not a
 *  section of the settings behind the reader's name. Which of them a Lemma
 *  uses is that Lemma's: its Connections. */
export function ConnectorsPage({ org }: { org: Org }) {
    return (
        <div className="ox-page ox-connpage">
            <h1 className="ox-title">Connectors</h1>
            <p className="ox-sub">What {org.name}’s Lemmas can work through. Each Lemma uses the ones it is given, under its Connections.</p>
            <ConnectorsSection orgId={org.id} />
        </div>
    );
}
