import type { PodConnector } from "@/data/pod-connectors";
import { BUILT, listed } from "@/site/plugins/catalog";

/** The accounts the apps just added read from, as rows to connect. */

/** An app that went in, as far as its accounts go. */
interface Added {
    slug: string;
    name: string;
}

/** One account to connect, as the apps that read it ask for it: Gmail or
 *  Outlook is one row with a button for each, not two accounts to find. */
export interface AccountRow {
    /** "Gmail or Outlook". */
    called: string;
    /** The apps it is for. */
    forApps: string[];
    /** What the apps say about it ("Mail sends a reply only when you press
     *  Send."), when they say something. */
    why: string;
    /** The Lemma's connectors that would answer it, still to connect. */
    connect: PodConnector[];
    /** One of them is connected already. */
    done: boolean;
}

/** The rows, from the Lemma's connectors that ask an account of me and the
 *  added apps' own words about which accounts they read. A connector no app
 *  names stands as a row of its own. */
export function accountRows(
    asked: readonly PodConnector[],
    missing: ReadonlySet<string>,
    added: readonly Added[],
    titleOf: (connectorId: string) => string,
): AccountRow[] {
    const rows: AccountRow[] = [];
    const placed = new Set<string>();
    for (const app of added) {
        const plugin = BUILT.find((one) => one.slug === app.slug);
        for (const account of plugin?.accounts ?? []) {
            /* In the order the app names them, so its first choice is the
               first button. */
            const wanted = account.any.map((name) => name.toLowerCase());
            const rank = (one: PodConnector) => wanted.indexOf(titleOf(one.connectorId).toLowerCase());
            const answering = asked.filter((one) => rank(one) >= 0).sort((a, b) => rank(a) - rank(b));
            if (answering.length === 0) continue;
            const called = listed(account.any, "or");
            const same = rows.find((row) => row.called === called);
            if (same) {
                if (!same.forApps.includes(app.name)) same.forApps.push(app.name);
                continue;
            }
            answering.forEach((one) => placed.add(one.slug));
            rows.push({
                called,
                forApps: [app.name],
                why: account.why,
                connect: answering.filter((one) => missing.has(one.slug)),
                done: answering.some((one) => !missing.has(one.slug)),
            });
        }
    }
    for (const one of asked) {
        if (placed.has(one.slug) || !missing.has(one.slug)) continue;
        rows.push({ called: titleOf(one.connectorId) || one.slug, forApps: [], why: one.purpose, connect: [one], done: false });
    }
    return rows;
}
