/* A space's contacts: the people its bots answer who are not in the space.
 *
 * Pure: the wire mappers and every sentence the Contacts place says, kept out
 * of the components so tests can read them without a DOM. */

import type {
    ContactListResponse,
    ContactResponse,
    ContactsCapResponse,
    WebWidgetResponse,
} from "lemma-sdk";

export type HandleKind = "PHONE" | "EMAIL" | "TELEGRAM" | "HOST" | "SLACK" | "TEAMS" | "WEB";

export interface ContactHandle {
    kind: HandleKind;
    value: string;
    strength: string;
}

export interface Contact {
    id: string;
    name: string | null;
    createdAt: string;
    handles: ContactHandle[];
    /** Known only by the browser they wrote from: nobody has vouched for them yet. */
    anonymous: boolean;
}

export type WidgetAnswer = "off" | "known" | "anyone";

/** The space's chat on a website — and the key a form page adds rows with. */
export interface WebWidget {
    id: string;
    name: string;
    publicKey: string;
    allowedOrigins: string[];
    answer: WidgetAnswer;
    lookedAfterBy: string | null;
    embed: string;
    /** A page Lemma hosts with the chat on it, to share as a link. */
    pageUrl: string;
}

/** A widget just made, with the one time its secret is shown. */
export interface NewWebWidget extends WebWidget {
    signingSecret: string;
}

/** What contacts can reach of the space beyond what is Public. */
export interface ContactReach {
    /** `contactColumns`: what a contact may read of their own rows, chosen by a member. */
    tables: { name: string; contactOwned: boolean; perPerson: boolean; contactColumns: string[] }[];
    functions: { name: string; description: string | null; contactsInvoke: boolean }[];
}

export interface ContactsCap {
    limit: number | null;
    spentThisMonth: number;
}

export interface WidgetDraft {
    name: string;
    origins: string;
    answer: WidgetAnswer;
}

export const EMPTY_WIDGET_DRAFT: WidgetDraft = { name: "", origins: "", answer: "anyone" };

const KINDS: HandleKind[] = ["PHONE", "EMAIL", "TELEGRAM", "HOST", "SLACK", "TEAMS", "WEB"];

export function readContact(wire: ContactResponse): Contact {
    return {
        id: wire.id,
        name: wire.display_name?.trim() || null,
        createdAt: wire.created_at,
        handles: (wire.identities ?? [])
            .filter((identity) => KINDS.includes(identity.kind as HandleKind))
            .map((identity) => ({
                kind: identity.kind as HandleKind,
                value: identity.value,
                strength: identity.strength,
            })),
        anonymous: wire.is_anonymous ?? false,
    };
}

export function readContacts(wire: ContactListResponse): { items: Contact[]; next: string | null } {
    return { items: (wire.items ?? []).map(readContact), next: wire.next_cursor ?? null };
}

/** The most pages one list follows: far past any space's contacts or tables,
 *  and short of a server that never stops answering "there's more". */
const MAX_PAGES = 50;

/** A whole list, page after page until the server says there is no next one.
 *  A first page alone said "these are all of them" when they were not. */
export async function everyPage<T>(page: (cursor: string | undefined) => Promise<{ items: T[]; next: string | null | undefined }>): Promise<T[]> {
    const items: T[] = [];
    const seen = new Set<string>();
    let cursor: string | undefined;
    for (let count = 0; count < MAX_PAGES; count += 1) {
        const got = await page(cursor);
        items.push(...got.items);
        if (!got.next || seen.has(got.next)) return items;
        seen.add(got.next);
        cursor = got.next;
    }
    return items;
}

export function readWidget(wire: WebWidgetResponse): WebWidget {
    return {
        id: wire.id,
        name: wire.name,
        publicKey: wire.public_key,
        allowedOrigins: wire.allowed_origins ?? [],
        answer: (["off", "known", "anyone"] as const).find((a) => a === wire.answer) ?? "off",
        lookedAfterBy: wire.looked_after_by ?? null,
        embed: wire.embed,
        pageUrl: wire.page_url,
    };
}

export function readCap(wire: ContactsCapResponse): ContactsCap {
    return { limit: wire.monthly_limit_usd ?? null, spentThisMonth: wire.spent_this_month_usd ?? 0 };
}

/* ── sentences ───────────────────────────────────────────────────────── */

/** How a contact is named in a list: their name, else their first handle. */
export function contactName(contact: Contact): string {
    if (contact.name) return contact.name;
    if (contact.anonymous) return "A visitor to your website";
    return contact.handles[0] ? handleText(contact.handles[0]) : "A contact";
}

/** One handle as a person reads it. A host id is the customer's own user id,
 *  so it says where it came from rather than showing the id. */
export function handleText(handle: ContactHandle): string {
    if (handle.kind === "PHONE") return "+" + handle.value.replace(/^\+/, "");
    if (handle.kind === "TELEGRAM") return "Telegram " + handle.value;
    if (handle.kind === "HOST") return "Signed in on your site";
    // A colleague in a workspace the organization holds is one too, so it
    // says only where they write from, not whose company they are in.
    if (handle.kind === "SLACK") return "Slack";
    if (handle.kind === "TEAMS") return "Teams, from another company";
    if (handle.kind === "WEB") return "Chat on your website";
    return handle.value;
}

/** Who vouched for a handle, said plainly. */
export function vouchedBy(handle: ContactHandle): string {
    switch (handle.strength) {
        case "CHANNEL": return handle.kind === "EMAIL" ? "Verified by their mail service" : "Verified by the platform";
        case "HOST": return "Named by your site";
        case "CODE": return "Confirmed with a code";
        case "MEMBER": return "Added by a member";
        case "SESSION": return "Not confirmed yet";
        default: return "";
    }
}

/** The second line of a contact's row. */
export function contactSubline(contact: Contact): string {
    if (contact.anonymous) return "Not confirmed yet";
    const handles = contact.handles.map(handleText);
    if (contact.name && handles.length) return handles.join(" · ");
    return handles.slice(1).join(" · ") || "No handle on record";
}

/** Whom a bot -- a WhatsApp number, a Telegram bot, an email address --
 *  answers when somebody messages it privately. Its members are always
 *  answered; the choice is who else is. Never its groups: whether a group's
 *  people outside the space are answered is the bot's own switch
 *  (`groupsNote`), so each note here says "privately". */
export function answerChoices(space: string): { value: WidgetAnswer; label: string; note: string }[] {
    return [
        { value: "off", label: "Members only", note: "Anyone outside " + space + " who writes privately is turned away." },
        { value: "known", label: "Members and contacts", note: "People already in Contacts are answered privately too. Strangers are turned away." },
        { value: "anyone", label: "Everyone", note: "Strangers are answered privately too, and join Contacts with their first message." },
    ];
}

/** What the private-message choice leaves to a bot's groups. */
export function groupsNote(space: string): string {
    return "Groups follow “Answer people outside " + space + "”.";
}

/** Whom a web chat answers. A visitor is nobody's member, so the choice is
 *  between any visitor and the people already in Contacts, who confirm
 *  their email (or are signed in by the site) before they may write. */
export function widgetAnswerChoices(): { value: WidgetAnswer; label: string; note: string }[] {
    return [
        { value: "anyone", label: "Every visitor", note: "Nobody has to sign in." },
        { value: "known", label: "Only people in Contacts", note: "Visitors confirm their email first, and strangers are turned away." },
        { value: "off", label: "Off", note: "Every visitor is turned away." },
    ];
}

/** A line written to a contact first, and what became of its send. */
export interface OutreachSent {
    channelId: string;
    lineId: string;
    platform: string | null;
    status: "PENDING" | "SENT" | "FAILED" | "SKIPPED" | "DEAD";
    reason: string | null;
}

/** The handles a member can write to first: an address, a number on
 *  WhatsApp, a Telegram account -- or, when they left none of those, their
 *  chats on the website, where the line waits for their next visit: the
 *  ones their site signed them in to first, then each browser's. Every one
 *  is offered, since any of them may be the chat that has ended. A
 *  colleague in another company's Slack or Teams waits for them to write. */
export function writableHandles(contact: Pick<Contact, "handles">): ContactHandle[] {
    const reached = contact.handles.filter((handle) => handle.kind === "EMAIL" || handle.kind === "PHONE" || handle.kind === "TELEGRAM");
    if (reached.length > 0) return reached;
    return [
        ...contact.handles.filter((handle) => handle.kind === "HOST"),
        ...contact.handles.filter((handle) => handle.kind === "WEB"),
    ];
}

/** Whether a line to this handle waits in their chat on the website. */
export function waitsOnTheSite(handle: Pick<ContactHandle, "kind">): boolean {
    return handle.kind === "HOST" || handle.kind === "WEB";
}

/** What a member is told after writing to a contact first. */
export function outreachSaid(sent: Pick<OutreachSent, "status" | "reason" | "platform">): string {
    if (sent.platform === "WEB" && sent.status === "SENT") return "In their chat on your website, for when they next open it.";
    if (sent.platform === "WEB" && sent.status === "PENDING") return "In their channel, on its way to their chat on your website.";
    const where = sent.platform === "WHATSAPP" ? "WhatsApp" : sent.platform === "TELEGRAM" ? "Telegram" : "email";
    switch (sent.status) {
        case "SENT": return "Sent by " + where + ". It is in their channel.";
        case "PENDING": return "In their channel, on its way by " + where + ".";
        case "SKIPPED": return "In their channel, but not sent" + (sent.reason ? ": " + sent.reason.replace(/\.$/, "") : "") + ".";
        default: return "In their channel, but it did not go" + (sent.reason ? ": " + sent.reason.replace(/\.$/, "") : "") + ". Retry it from there.";
    }
}

/** Why writing to a contact first was refused, in words a member can act
 *  on. The API's refusals of the message itself -- they asked not to be
 *  written to there, WhatsApp's window has closed, the pod has no address to
 *  write from -- are already sentences, and are said as given; anything
 *  else (a server fault, no connection, a body that is not a sentence) is
 *  said here, never shown raw. */
export function outreachProblem(error: unknown): string {
    const status = error && typeof error === "object" ? (error as { statusCode?: unknown }).statusCode : undefined;
    const message = error instanceof Error ? error.message.trim() : "";
    const sentence = message.length > 0 && message.length < 240 && /^[A-Z][^{}<>\n]*[.!?]$/.test(message) && !/^(Request failed|HTTP|Internal Server Error|Bad Request|Conflict|Unprocessable)/.test(message);
    switch (status) {
        case 404: return "They are not one of this pod’s contacts any more.";
        case 403: return "Only an editor of the pod can write to contacts.";
        case 409:
        case 422: return sentence ? message : status === 409 ? "They can’t be written to there now." : "That can’t be sent to them there.";
        case 429: return "Too many sent just now. Wait a minute and try again.";
        case 503: return "Sending isn’t available right now. Try again in a few minutes.";
        default:
            if (typeof status === "number" && status >= 500) return "Lemma couldn’t send it just now. Try again.";
            return status === undefined && error instanceof Error && error.name === "NetworkError"
                ? "Couldn’t reach Lemma. Check your connection and try again."
                : "Couldn’t send it. Try again.";
    }
}

/** "$3.20 of $25 this month", or what was spent with no cap set. */
export function sayCap(cap: ContactsCap): string {
    const spent = money(cap.spentThisMonth);
    return cap.limit == null ? spent + " this month, no cap set" : spent + " of " + money(cap.limit) + " this month";
}

function money(value: number): string {
    return "$" + (value < 10 ? value.toFixed(2) : Math.round(value).toString());
}

/** The origins typed into a box, one per line or comma, tidied the way the
 *  API stores them. */
export function readOrigins(text: string): string[] {
    return Array.from(new Set(text.split(/[\s,]+/).map((origin) => origin.trim().replace(/\/+$/, "").toLowerCase()).filter(Boolean)));
}

/** What is wrong with a draft, or null when it can be saved. */
export function widgetProblem(draft: WidgetDraft): string | null {
    if (!draft.name.trim()) return "Give it a name.";
    const bad = readOrigins(draft.origins).find((origin) => !/^(https:\/\/|http:\/\/localhost|http:\/\/127\.0\.0\.1)/.test(origin));
    if (bad) return bad + " needs to start with https://";
    return null;
}
