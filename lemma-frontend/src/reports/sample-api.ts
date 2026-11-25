import type { ReportApi } from "./api";
import type { Blockable } from "./model";

/** The sample's reports and blocks, answering the same calls the API does.
 *  A report is taken and goes nowhere -- there is no team behind the sample
 *  -- so what the dialog says once it is sent can be read. A block is kept
 *  for as long as the tab is open, and the sample's chats
 *  (`channels/sample-api`) show that person's lines as blocked until it is
 *  lifted. The sample's Lemma answers nobody, so stopping it answering a
 *  contact has nothing to stop. */

type Who = Pick<Blockable, "kind" | "id">;

const blocked = new Set<string>();

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const keyOf = (who: Who) => who.kind + ":" + who.id;

/** Whether the reader blocked whoever a sample line's sender names. */
export function blockedInSample(sender: unknown): boolean {
    if (!sender || typeof sender !== "object") return false;
    const { kind, id } = sender as { kind?: unknown; id?: unknown };
    return (kind === "MEMBER" || kind === "CONTACT") && typeof id === "string" && blocked.has(kind + ":" + id);
}

export function sampleReportApi(_podId: string): ReportApi {
    return {
        async report() {
            await wait(250);
        },
        async block(who) {
            await wait(200);
            blocked.add(keyOf(who));
        },
        async unblock(who) {
            await wait(200);
            if (!blocked.delete(keyOf(who))) {
                throw Object.assign(new Error("That block is not here any more."), { statusCode: 404 });
            }
        },
        async setContactBlocked() {
            await wait(200);
        },
    };
}
