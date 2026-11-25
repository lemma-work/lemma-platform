import { SAMPLE_LEMMAS, sampleSource } from "../../../data/sample";
import { filesSample } from "../../files/sample";
import { tablesSample } from "../../tables/sample";
import { rowMatches } from "./model";
import type { SearchSource } from "./source";

/** Search's own reads as sample data. Everything a hit opens is already in
 *  another area's sample, so these read those samples rather than make up
 *  a second world: the apps Home lists, the files each Lemma starts with,
 *  the rows of the sample tables. Only the functions are new, and they are
 *  the ones the sample workflows call (`work/sample.ts`).
 *
 *  Hiring's files cannot be searched the first time they are asked for, so
 *  "Couldn’t reach files in Hiring" and its Try again can be seen. */

const [SUPPORT, LAUNCH, RENEWALS, HIRING, BOOKS] = SAMPLE_LEMMAS;

const FUNCTIONS: Record<string, { name: string; description: string }[]> = {
    [SUPPORT.id]: [
        { name: "find_unanswered", description: "Finds tickets nobody has answered in a day." },
        { name: "issue_refund", description: "Refunds an order, in part or in whole." },
        { name: "lookup_order", description: "Finds an order by its number or the customer’s email." },
    ],
    [LAUNCH.id]: [{ name: "post_launch_update", description: "Posts the day’s progress to launch-week." }],
    [RENEWALS.id]: [{ name: "renewal_reminder", description: "Emails the account owner a week before a renewal." }],
    [HIRING.id]: [{ name: "schedule_interview", description: "Finds a time that suits the whole panel." }],
    [BOOKS.id]: [
        { name: "create_supplier", description: "Adds a supplier from the onboarding form." },
        { name: "reconcile_payouts", description: "Matches each payout against its invoices." },
    ],
};

const settle = <T,>(value: T, ms = 140) => new Promise<T>((done) => setTimeout(() => done(value), ms));

export function searchSample(): SearchSource {
    const files = filesSample();
    const tables = tablesSample();
    const core = sampleSource();
    let refused = false;
    return {
        async functions(podId) {
            return settle({ items: (FUNCTIONS[podId] ?? []).map((one) => ({ id: `fn-${one.name}`, ...one })) });
        },
        async apps(podId) {
            const all = await core.apps("sample-acme", SAMPLE_LEMMAS);
            return settle({ items: all.filter((app) => app.podId === podId).map((app) => ({ id: app.id, name: app.name, description: app.description, url: app.url })) });
        },
        async files(podId, query, limit) {
            if (podId === HIRING.id && !refused) {
                refused = true;
                await settle(null, 500);
                throw new Error("The search service did not answer in time.");
            }
            const hits = await files.search(podId, query, "/");
            return settle({ items: hits.slice(0, limit).map((hit, index) => ({ path: hit.path, content: hit.snippet, score: 1 - index / 100 })) }, 320);
        },
        async records(podId, table, query) {
            const page = await tables.rows(podId, table.tableName);
            return settle(page.items.filter((row) => rowMatches(row, table.searchFields, query)).slice(0, table.limit), 260);
        },
        async discuss() {
            /* No chat is bound to anything in sample data: a new one opens,
               with the words in its box. */
            return null;
        },
    };
}
