"use client";

import { listed, type BuiltPlugin } from "@/site/plugins/catalog";
import { GlyphIcon } from "./glyphs";
import type { InstallRun } from "./install-run";
import { stepWords } from "./install-state";
import { TickIcon } from "./shell";

/** The apps going in, as one quiet strip over whatever else a step asks:
 *  the one being set up and what it is doing, how many are ready, and that
 *  nobody needs to wait for it. The server carries on if the page closes,
 *  and the Lemma says so in their chat when they are all in. */
export function InstallStrip({ plugins, run }: { plugins: readonly BuiltPlugin[]; run: InstallRun }) {
    if (plugins.length === 0) return null;
    const rowOf = (one: BuiltPlugin) => run.rows[one.slug];
    const ready = plugins.filter((one) => rowOf(one)?.kind === "added");
    const now = plugins.find((one) => rowOf(one)?.kind === "adding") ?? null;
    const next = plugins.find((one) => !rowOf(one) || rowOf(one)?.kind === "waiting") ?? null;
    const stopped = plugins.filter((one) => rowOf(one)?.kind === "failed" || rowOf(one)?.kind === "asks");
    const share = run.stage.reduce((sum, one) => sum + one.progress, 0) / plugins.length;
    const tally = `${ready.length} of ${plugins.length} ready`;

    let what: string;
    let more: string;
    if (run.done) {
        what = plugins.length === 1 ? `${plugins[0].name} is ready` : `All ${plugins.length} apps are ready`;
        more = "Open any of them whenever you like.";
    } else if (now) {
        what = `Setting up ${now.name}`;
        more = `${stepWords(rowOf(now)?.step ?? null)}. ${plugins.length > 1 ? `${tally}; ` : ""}I’ll keep going while you look around.`;
    } else if (next && run.running) {
        what = `Getting ${next.name} ready`;
        more = `${plugins.length > 1 ? `${tally}; ` : ""}I’ll keep going while you look around.`;
    } else if (stopped.length > 0) {
        what = `${listed(stopped.map((one) => one.name))} ${stopped.length === 1 ? "needs" : "need"} a hand`;
        more = "Try again picks up where it stopped. Nothing done so far is lost.";
    } else {
        what = `Getting your apps ready`;
        more = "I’ll keep going while you look around.";
    }

    return (
        <div className="arin" data-done={run.done ? "" : undefined} aria-label="Your apps">
            <span className="arin__apps">
                {plugins.map((one) => (
                    <span key={one.slug} className="arin__tile" data-state={rowOf(one)?.kind ?? "waiting"}><GlyphIcon slug={one.slug} size={14} /></span>
                ))}
            </span>
            <span className="arin__said">
                <span className="arin__what" role="status">{what}</span>
                <span className="arin__more">{more}</span>
            </span>
            {run.done
                ? <span className="arin__check"><TickIcon size={11} /></span>
                : stopped.length > 0 && !run.running
                    ? <button type="button" className="ar__btn ar__btn--small" onClick={run.retry}>Try again</button>
                    : <span className="arin__pct">{Math.round(share * 100)}%</span>}
            <span className="arin__bar" style={{ gridTemplateColumns: `repeat(${plugins.length}, 1fr)` }}>
                {run.stage.map((one) => <span key={one.slug}><i style={{ width: `${Math.max(one.progress > 0 ? 3 : 0, one.progress * 100)}%` }} /></span>)}
            </span>
        </div>
    );
}
