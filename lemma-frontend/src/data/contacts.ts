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

export type HandleKind = "PHONE" | "EMAIL" | "TELEGRAM" | "HOST";

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

const KINDS: HandleKind[] = ["PHONE", "EMAIL", "TELEGRAM", "HOST"];

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
    };
}

export function readContacts(wire: ContactListResponse): { items: Contact[]; next: string | null } {
    return { items: (wire.items ?? []).map(readContact), next: wire.next_before ?? null };
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
    return contact.name || (contact.handles[0] ? handleText(contact.handles[0]) : "A contact");
}

/** One handle as a person reads it. A host id is the customer's own user id,
 *  so it says where it came from rather than showing the id. */
export function handleText(handle: ContactHandle): string {
    if (handle.kind === "PHONE") return "+" + handle.value.replace(/^\+/, "");
    if (handle.kind === "TELEGRAM") return "Telegram " + handle.value;
    if (handle.kind === "HOST") return "Signed in on your site";
    return handle.value;
}

/** Who vouched for a handle, said plainly. */
export function vouchedBy(handle: ContactHandle): string {
    switch (handle.strength) {
        case "CHANNEL": return handle.kind === "EMAIL" ? "Verified by their mail service" : "Verified by the platform";
        case "HOST": return "Named by your site";
        case "CODE": return "Confirmed with a code";
        case "MEMBER": return "Added by a member";
        default: return "";
    }
}

/** The second line of a contact's row. */
export function contactSubline(contact: Contact): string {
    const handles = contact.handles.map(handleText);
    if (contact.name && handles.length) return handles.join(" · ");
    return handles.slice(1).join(" · ") || "No handle on record";
}

/** What each answer means, for a bot or a widget, in one line. */
export function answerChoices(space: string): { value: WidgetAnswer; label: string; note: string }[] {
    return [
        { value: "off", label: "Only people in " + space, note: "Anybody else is turned away, as now." },
        { value: "known", label: "People in " + space + " and its contacts", note: "Strangers are turned away." },
        { value: "anyone", label: "Anyone who writes", note: "A stranger becomes a contact with their first message." },
    ];
}

/** Why a follow-up was refused, from the code the API gave. */
export function followUpRefusal(code: string | undefined): string {
    switch (code) {
        case "unsubscribed": return "They asked not to be written to there.";
        case "outside_window": return "WhatsApp only allows a reply within a day of their last message.";
        case "no_conversation": return "They haven’t written yet, so there is nowhere to write back.";
        case "no_handle": return "There is no way to reach them on that channel.";
        default: return "Couldn’t send it. Try again.";
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
