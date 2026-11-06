"use client";

import type { Pod } from "@/data";
import type { Go } from "./model";
import { AppTiles, useApps } from "./ask";
import { Empty } from "./parts";

/** Every app across the Lemmas, newest first: what people use their work
 *  through, one place in the sidebar rather than a shelf on every page. */
export function AppsPage({ pods, go }: { pods: readonly Pod[]; go: Go }) {
    const { apps, pending } = useApps(pods);
    return (
        <div className="ox-page ox-appspage">
            <h1 className="ox-title">Apps</h1>
            {apps.length > 0
                ? <AppTiles apps={apps} go={go} />
                : pending
                    ? <p className="ox-vs">Loading apps…</p>
                    : <Empty title="No apps yet."><p>Ask a Lemma to build one, and it shows here.</p></Empty>}
        </div>
    );
}
