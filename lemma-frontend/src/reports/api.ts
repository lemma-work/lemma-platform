import { lemma } from "@/session/client";
import { blockPath, reportBody, reportPath, type Blockable, type ReportReason, type ReportTarget } from "./model";

/** The report and block calls, through the session's client. */

export async function sendReport(podId: string, target: ReportTarget, reason: ReportReason, note: string): Promise<void> {
    await lemma(podId).request("POST", reportPath(podId, target), { body: reportBody(reason, note) });
}

export async function block(podId: string, who: Pick<Blockable, "kind" | "id">): Promise<void> {
    await lemma(podId).request("PUT", blockPath(podId, who));
}

export async function unblock(podId: string, who: Pick<Blockable, "kind" | "id">): Promise<void> {
    await lemma(podId).request("DELETE", blockPath(podId, who));
}

/** Stop the Lemma's agents answering a contact, or let them again. */
export async function setContactBlocked(podId: string, contactId: string, blocked: boolean): Promise<void> {
    await lemma(podId).request("PATCH", `/pods/${podId}/contacts/${contactId}`, { body: { blocked } });
}
