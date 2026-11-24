import { source } from "@/data";
import { lemma } from "@/session/client";
import { blockPath, reportBody, reportPath, type Blockable, type ReportReason, type ReportTarget } from "./model";

/** The report and block calls. One seam, as the channels' (`channels/api`),
 *  so the sample source can answer them from its fixtures. */
export interface ReportApi {
    report(target: ReportTarget, reason: ReportReason, note: string): Promise<void>;
    block(who: Pick<Blockable, "kind" | "id">): Promise<void>;
    unblock(who: Pick<Blockable, "kind" | "id">): Promise<void>;
    /** Stop the Lemma's agents answering a contact, or let them again. */
    setContactBlocked(contactId: string, blocked: boolean): Promise<void>;
}

function liveApi(podId: string): ReportApi {
    const client = () => lemma(podId);
    return {
        async report(target, reason, note) {
            await client().request("POST", reportPath(podId, target), { body: reportBody(reason, note) });
        },
        async block(who) {
            await client().request("PUT", blockPath(podId, who));
        },
        async unblock(who) {
            await client().request("DELETE", blockPath(podId, who));
        },
        async setContactBlocked(contactId, blocked) {
            await client().request("PATCH", `/pods/${podId}/contacts/${contactId}`, { body: { blocked } });
        },
    };
}

/** The sample's answers, loaded only when asked. */
function sampleApi(podId: string): ReportApi {
    return new Proxy({} as ReportApi, {
        get: (_target, name) => async (...args: unknown[]) => {
            const { sampleReportApi } = await import("./sample-api");
            const api = sampleReportApi(podId) as unknown as Record<string, (...given: unknown[]) => Promise<unknown>>;
            return api[String(name)](...args);
        },
    });
}

export function reportApi(podId: string): ReportApi {
    return source.label === "sample" ? sampleApi(podId) : liveApi(podId);
}

export async function sendReport(podId: string, target: ReportTarget, reason: ReportReason, note: string): Promise<void> {
    await reportApi(podId).report(target, reason, note);
}

export async function block(podId: string, who: Pick<Blockable, "kind" | "id">): Promise<void> {
    await reportApi(podId).block(who);
}

export async function unblock(podId: string, who: Pick<Blockable, "kind" | "id">): Promise<void> {
    await reportApi(podId).unblock(who);
}

/** Stop the Lemma's agents answering a contact, or let them again. */
export async function setContactBlocked(podId: string, contactId: string, blocked: boolean): Promise<void> {
    await reportApi(podId).setContactBlocked(contactId, blocked);
}
