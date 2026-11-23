/** Reporting what was said, and blocking a person, in the app's own words.
 *
 *  A report goes to the team that runs Lemma (`reports` on the server): a
 *  Lemma's reply, from a person's own chat with it, or anybody's line in a
 *  shared chat. A block is the reader's alone: the person's lines are hidden
 *  from them in this Lemma's chats, and nobody else's view changes. Stopping
 *  the Lemma answering a contact is a separate thing, for whoever may change
 *  the Lemma, and lives on the contact.
 *
 *  Pure, so the tests can hold every sentence to what the server takes. */

export type ReportReason = "OFFENSIVE" | "HARMFUL" | "SEXUAL" | "SPAM" | "OTHER";

/** In the order a dialog offers them. */
export const REPORT_REASONS: readonly { id: ReportReason; label: string }[] = [
    { id: "OFFENSIVE", label: "Offensive or hateful" },
    { id: "HARMFUL", label: "Harmful or dangerous" },
    { id: "SEXUAL", label: "Sexual" },
    { id: "SPAM", label: "Spam or a scam" },
    { id: "OTHER", label: "Something else" },
];

/** What the server keeps of a reporter's own words. */
export const MAX_REPORT_NOTE = 1000;

/** What is being reported: a reply in a chat with a Lemma, or a line of a
 *  shared chat. */
export type ReportTarget =
    | { kind: "reply"; conversationId: string; messageId: string }
    | { kind: "line"; channelId: string; lineId: string };

export function reportPath(podId: string, target: ReportTarget): string {
    return target.kind === "reply"
        ? `/pods/${podId}/conversations/${target.conversationId}/messages/${target.messageId}/report`
        : `/pods/${podId}/channels/${target.channelId}/messages/${target.lineId}/report`;
}

export interface ReportBody {
    reason: ReportReason;
    note?: string;
}

/** The request as sent: the words trimmed, none sent when there are none. */
export function reportBody(reason: ReportReason, note: string): ReportBody {
    const words = note.trim().slice(0, MAX_REPORT_NOTE);
    return words ? { reason, note: words } : { reason };
}

/** A person who can be blocked: a member of the Lemma or one of its
 *  contacts, known by an id. */
export interface Blockable {
    kind: "MEMBER" | "CONTACT";
    id: string;
    name: string;
}

export function blockPath(podId: string, who: Pick<Blockable, "kind" | "id">): string {
    return `/pods/${podId}/blocks/${who.kind}:${who.id}`;
}

/** Who wrote a line, as somebody the reader could block: never a Lemma's
 *  agent, never the reader, never somebody with no id -- a person a platform
 *  did not vouch for, or a member since removed. */
export function blockable(
    sender: { kind: "MEMBER" | "CONTACT" | "AGENT"; id: string | null; name: string | null },
    me: string | null,
): Blockable | null {
    if (sender.kind === "AGENT" || !sender.id) return null;
    if (sender.kind === "MEMBER" && sender.id === me) return null;
    return { kind: sender.kind, id: sender.id, name: sender.name?.trim() || "this person" };
}

/** Why Block is not offered on somebody's line, when it is not: said in the
 *  row rather than leaving the reader to wonder where it went. */
export function cannotBlock(sender: { kind: "MEMBER" | "CONTACT" | "AGENT"; id: string | null }): string | null {
    if (sender.kind === "CONTACT" && !sender.id) return "Their platform didn’t confirm who they are, so there’s no one to block. Reporting still works.";
    return null;
}

/** A server's id rather than one the app made up for a row it drew first. */
export function isServerId(id: string): boolean {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
}

/* ── what the dialogs say ───────────────────────────────────────────── */

export function reportTitle(target: ReportTarget, author: string | null): string {
    if (target.kind === "reply") return "Report this reply";
    return author ? `Report ${author}’s message` : "Report this message";
}

export function reportLine(target: ReportTarget): string {
    return target.kind === "reply"
        ? "The Lemma team will read this reply and the message that asked for it."
        : "The Lemma team will read this message and the few before it. Nobody in the chat is told.";
}

export const REPORTED_LINE = "Thanks. The Lemma team has your report. If they need more, they’ll write to you at the email you sign in with.";

export function blockTitle(who: Blockable): string {
    return `Block ${who.name}?`;
}

export function blockLine(who: Blockable, lemma: string): string {
    return `You won’t see ${who.name}’s messages in ${lemma}’s chats, and you won’t hear about them. ${who.name} isn’t told, and everyone else in the chat still sees them.`;
}

export function stopAnsweringLabel(who: Blockable, lemma: string): string {
    return `Stop ${lemma} answering ${who.name}`;
}

export function stopAnsweringLine(who: Blockable, lemma: string): string {
    return `${lemma} won’t answer ${who.name} on WhatsApp, Telegram or email, and won’t write to them first. Their messages still come into the chat.`;
}

export function memberBlockNote(who: Blockable, lemma: string): string {
    return `${who.name} is in ${lemma}. To take them out, an admin removes them in Settings › People.`;
}

export function blockedNote(name: string | null, count = 1): string {
    const who = name?.trim() || "someone";
    return count > 1
        ? `${count} messages from ${who}, hidden because you blocked them.`
        : `A message from ${who}, hidden because you blocked them.`;
}
