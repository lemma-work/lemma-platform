import { SAMPLE_LEMMAS, samplePulse } from "../../data/sample";
import type { ConversationRef } from "../../data/types";
import { SAMPLE_PNG } from "../files/sample-media";
import { SAMPLE_PHOTOS, SAMPLE_SIZES, SAMPLE_VOICEMAIL } from "../chat/sample-paths";
import type { AgentRef, BotAnswer, ChannelChange, ChatsSource, LemmaPerson, MineChange, PostBody, WaitingRun } from "./source";
import type { Contact, ContactReach, WebWidget } from "@web/data/contacts";
import type { Channel, ChannelDetail, Line, LineDelivery, Member, Part, SourcePlace } from "@web/channels/model";
import type { SearchHit } from "@web/channels/search-model";
import type { SourceSettings } from "@web/channels/sources";
import type { Surface } from "@web/data/types";
import type { Inbox, MentionItem, PersonRef, WaitingQuestion } from "@web/channels/waiting";

/** Which drawn file each sample message's file is. */
const SAMPLE_LINE_FILES: Record<string, string> = {
    ...Object.fromEntries(SAMPLE_PHOTOS.map((photo) => ["file-" + photo.name, photo.path])),
    "file-voicemail": SAMPLE_VOICEMAIL,
};

/** Chats across Lemmas, groups, people and contacts, as sample data: shaped
 *  like a real Lemma, never a real one. Every state a chat screen draws is in
 *  one of these, so each can be looked at without a server:
 *
 *  - Support: Ada's own line with it on WhatsApp; "Customer care", a group
 *    mirrored from a WhatsApp group, with a contact in it, a question waiting
 *    on Ada, a thread, a delivery that failed, a message removed by Sam, an
 *    answer kept private and a picture; three contacts waiting on a person;
 *    a long history of chats; contacts, a web chat and what contacts reach.
 *  - Launch: "launch-week", a private group whose Lemma answers every message,
 *    naming Ada, with an approval and a form waiting on her, an answer only she
 *    reads, edits, a thread, and Dev typing (Sam in the thread) for a while
 *    after it opens; "Press friends", a Telegram group.
 *
 *  Held in memory, so what is sent, edited, read or changed stays for as long
 *  as the app is open. The ids are the core sample's (`data/sample.ts`), so a
 *  row on the Lemmas tab opens the same chat here. */

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const [SUPPORT, LAUNCH] = SAMPLE_LEMMAS;

const ME: PersonRef = { kind: "MEMBER", id: "me", name: "Ada Lovelace" };
const SAM: PersonRef = { kind: "MEMBER", id: "sam", name: "Sam Rivera" };
const DEV: PersonRef = { kind: "MEMBER", id: "dev", name: "Dev Mehta" };
const PRIYA: PersonRef = { kind: "MEMBER", id: "priya", name: "Priya Shah" };
const MARIA: PersonRef = { kind: "CONTACT", id: "contact-maria", name: "Maria Chen" };
const OMAR: PersonRef = { kind: "CONTACT", id: "contact-omar", name: "Omar Haddad" };
const LI: PersonRef = { kind: "CONTACT", id: "contact-li", name: "Li Wei" };
const NEWS: PersonRef = { kind: "CONTACT", id: "contact-june", name: "June Park" };

const agentOf = (podId: string, name: string): PersonRef => ({ kind: "AGENT", id: "agent-" + podId, name });
const SUPPORT_AGENT = agentOf(SUPPORT.id, SUPPORT.name);
const LAUNCH_AGENT = agentOf(LAUNCH.id, LAUNCH.name);
const RESEARCHER: PersonRef = { kind: "AGENT", id: "agent-researcher", name: "Researcher" };

const PEOPLE: LemmaPerson[] = [
    { userId: "me", name: "Ada Lovelace", email: "ada@acme.example", role: "Admin" },
    { userId: "sam", name: "Sam Rivera", email: "sam@acme.example", role: "Editor" },
    { userId: "dev", name: "Dev Mehta", email: "dev@acme.example", role: "Editor" },
    { userId: "priya", name: "Priya Shah", email: "priya@acme.example", role: "User" },
];

function iso(ago: number, now: number): string {
    return new Date(now - ago).toISOString();
}

interface Held {
    podId: string;
    channel: ChannelDetail;
    lines: Line[];
    replies: Map<string, Line[]>;
}

function blank(): Omit<Line, "id" | "seq" | "sender" | "text" | "createdAt"> {
    return {
        onBehalfOf: null, editedAt: null, deleted: false, removedBy: null, withheld: false, visibility: "channel", canShare: false,
        membersOnly: false, sourceId: null, threadRootId: null, replyToId: null, replyCount: 0, lastReplyAt: null,
        interaction: null, parts: [], clientMessageId: null, delivered: null,
    };
}

function say(seq: number, sender: PersonRef, text: string | null, at: string, extra: Partial<Line> = {}): Line {
    return { ...blank(), id: "sl-" + Math.random().toString(36).slice(2, 10), seq, sender, text, createdAt: at, ...extra };
}

function channelOf(id: string, kind: Channel["kind"], extra: Partial<ChannelDetail>, now: number): ChannelDetail {
    return {
        id, kind, title: null, topic: null, person: null, visibility: kind === "GROUP" ? "PUBLIC" : "PRIVATE", answersShared: true,
        sources: [], assignee: null, agentsAnswer: true, handBackAfterHours: 12, status: kind === "CONTACT" ? "open" : null,
        attention: null, archived: false, joined: true, lastLine: null, lastMessageAt: null, lastSeq: 0, createdAt: iso(40 * DAY, now),
        unread: { lines: 0, mentions: 0, waiting: 0, readSeq: 0 },
        places: [], members: [],
        me: { joined: true, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 0, role: "member", canManage: true },
        ...extra,
    };
}

const member = (person: PersonRef, role: Member["role"] = "member"): Member => ({ person, role, answers: null });
const seat = (person: PersonRef, answers: Member["answers"] = "MENTIONED"): Member => ({ person, role: "member", answers });

function place(id: string, platform: string, kind: SourcePlace["kind"], title: string | null, at: string): SourcePlace {
    return { id, platform, kind, title, surfaceId: "surface-" + platform.toLowerCase(), pending: false, lastInboundAt: at };
}

function mention(person: PersonRef): Part {
    return { type: "mention", kind: person.kind, id: person.id ?? "", name: person.name };
}

const sent = (sourceId: string, platform: string, status: LineDelivery["status"] = "SENT", reason: string | null = null): LineDelivery => ({
    id: "delivery-" + Math.random().toString(36).slice(2, 8), sourceId, platform, status, reason,
});

/* ── the store ─────────────────────────────────────────────────────── */

function build(now: number): Held[] {
    const held: Held[] = [];
    const at = (ago: number) => iso(ago, now);

    /* Support: Ada's own line, on WhatsApp. */
    {
        const lines = [
            say(1, SUPPORT_AGENT, "Morning digest: 9 customers heard back overnight, 2 are waiting on a person.", at(9 * HOUR)),
            say(2, ME, "Who is waiting longest?", at(8 * HOUR), { sourceId: "support-line-wa" }),
            say(3, SUPPORT_AGENT, "Li Wei, since yesterday afternoon. They asked about an invoice we sent twice.", at(8 * HOUR - MIN), { onBehalfOf: ME }),
            say(4, SUPPORT_AGENT, "Northwind’s refund needs your yes.", at(12 * MIN)),
            say(5, SUPPORT_AGENT, "Maria Chen asked when the replacement lamp arrives. I said a person would confirm.", at(3 * MIN)),
        ];
        held.push({
            podId: SUPPORT.id,
            channel: channelOf("support-line", "DIRECT", {
                person: ME, sources: [{ platform: "WHATSAPP", title: null }],
                places: [place("support-line-wa", "WHATSAPP", "PERSON", null, at(8 * HOUR))],
                members: [member(ME, "owner"), seat(SUPPORT_AGENT, "EVERY_LINE")],
                unread: { lines: 2, mentions: 0, waiting: 0, readSeq: 3 },
                me: { joined: true, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 3, role: "owner", canManage: false },
            }, now),
            lines,
            replies: new Map(),
        });
    }

    /* Support: a group mirrored from WhatsApp, with a contact in it. */
    {
        const wa = "care-wa";
        const thread = say(3, SAM, "Do we still have the brass lamp in stock?", at(5 * HOUR), { replyCount: 2, lastReplyAt: at(4 * HOUR) });
        const lines = [
            say(1, MARIA, "Hi, our order 4471 arrived with one lamp cracked. Photos below, and I left a voicemail too.", at(26 * HOUR), { sourceId: wa, parts: [
                ...SAMPLE_PHOTOS.slice(0, 3).map((photo): Part => ({ type: "file", path: photo.path, name: photo.name, mimeType: "image/jpeg", size: null, fileId: "file-" + photo.name })),
                { type: "file", path: SAMPLE_VOICEMAIL, name: "Voicemail from Maria.m4a", mimeType: "audio/mp4", size: SAMPLE_SIZES[SAMPLE_VOICEMAIL], fileId: "file-voicemail" },
            ] }),
            say(2, SUPPORT_AGENT, "Sorry to hear that, Maria. A cracked lamp in transit is covered: we’ll replace it or refund it, whichever you prefer.", at(26 * HOUR - 2 * MIN), { sourceId: wa, onBehalfOf: MARIA, replyToId: null }),
            thread,
            say(4, SUPPORT_AGENT, "Should I offer Maria a replacement or a refund?", at(2 * HOUR), {
                onBehalfOf: ME,
                interaction: { kind: "ASK", state: "OPEN", question: { questions: [{ header: "choice", question: "Replacement or refund?", options: [{ label: "Replacement" }, { label: "Refund" }] }] }, awaits: ME, answer: null, answeredBy: null, answeredAt: null },
            }),
            say(5, ME, "I’ll check the warehouse and come back to you today.", at(90 * MIN), { sourceId: wa, delivered: [sent(wa, "WHATSAPP")] }),
            say(6, SAM, null, at(80 * MIN), { deleted: true, removedBy: SAM }),
            say(7, ME, "Photos are with the carrier now.", at(70 * MIN), { sourceId: wa, delivered: [sent(wa, "WHATSAPP", "DEAD", "outside_window")] }),
            say(8, SUPPORT_AGENT, null, at(50 * MIN), { withheld: true, onBehalfOf: PRIYA }),
            say(9, SUPPORT_AGENT, "Your refunds this month: 3 approved, $412 in all. The largest was Northwind’s.", at(40 * MIN), { onBehalfOf: ME, visibility: "asker_only", canShare: true }),
            say(10, PRIYA, "Thanks " + "@Ada Lovelace" + ", I’ll tell the shop floor.", at(20 * MIN), { parts: [mention(ME)] }),
        ];
        const replies = [
            say(11, PRIYA, "Two left in the back room.", at(4 * HOUR + 30 * MIN), { threadRootId: thread.id }),
            say(12, SAM, "Great, holding one for Maria.", at(4 * HOUR), { threadRootId: thread.id }),
        ];
        held.push({
            podId: SUPPORT.id,
            channel: channelOf("customer-care", "GROUP", {
                title: "Customer care", topic: "Escalations from the shop",
                sources: [{ platform: "WHATSAPP", title: "Acme × Northwind" }],
                places: [place(wa, "WHATSAPP", "CHAT", "Acme × Northwind", at(26 * HOUR))],
                members: [member(ME, "owner"), member(SAM), member(PRIYA), member(MARIA, "guest"), seat(SUPPORT_AGENT)],
                unread: { lines: 3, mentions: 1, waiting: 1, readSeq: 7 },
                me: { joined: true, notify: "MENTIONS", notifyVia: null, mutedUntil: null, readSeq: 7, role: "owner", canManage: true },
            }, now),
            lines,
            replies: new Map([[thread.id, replies]]),
        });
    }

    /* Support: contacts waiting on a person (the core sample's three). */
    const contact = (id: string, who: PersonRef, extra: Partial<ChannelDetail>, said: Line[]) => {
        held.push({
            podId: SUPPORT.id,
            channel: channelOf(id, "CONTACT", {
                person: who, title: who.name, members: [member(who, "guest"), seat(SUPPORT_AGENT, "EVERY_LINE")],
                me: { joined: false, notify: "MENTIONS", notifyVia: null, mutedUntil: null, readSeq: 0, role: null, canManage: true },
                ...extra,
            }, now),
            lines: said,
            replies: new Map(),
        });
    };
    contact("support-maria", MARIA, {
        sources: [{ platform: "WHATSAPP", title: null }], places: [place("maria-wa", "WHATSAPP", "PERSON", null, at(3 * HOUR))],
        attention: { since: at(3 * HOUR), reason: "About delivery dates" }, agentsAnswer: false,
        unread: { lines: 1, mentions: 0, waiting: 0, readSeq: 1 },
    }, [
        say(1, MARIA, "Hi, when will the replacement lamp arrive?", at(3 * HOUR + 2 * MIN), { sourceId: "maria-wa" }),
        say(2, SUPPORT_AGENT, "I’ve asked a person to confirm the delivery date for you.", at(3 * HOUR), { sourceId: "maria-wa", delivered: null }),
    ]);
    contact("support-omar", OMAR, {
        sources: [{ platform: "RESEND", title: null }], places: [place("omar-mail", "RESEND", "PERSON", null, at(9 * HOUR))],
        attention: { since: at(9 * HOUR), reason: "Wants a person" },
    }, [
        say(1, OMAR, "Can I speak to someone about the bulk order? I’d rather not go through the bot.", at(9 * HOUR), { sourceId: "omar-mail" }),
        say(2, SUPPORT_AGENT, "Of course. Someone from Support will write back today.", at(9 * HOUR - MIN), { sourceId: "omar-mail" }),
    ]);
    contact("support-li", LI, {
        sources: [{ platform: "WEB", title: null }], places: [place("li-web", "WEB", "PERSON", null, at(30 * HOUR))],
        attention: { since: at(30 * HOUR), reason: "Asked about invoices" }, status: "open",
    }, [
        say(1, LI, "I was invoiced twice for March. Can someone look?", at(30 * HOUR), { sourceId: "li-web" }),
    ]);
    contact("support-june", NEWS, {
        sources: [{ platform: "RESEND", title: null }], places: [place("june-mail", "RESEND", "PERSON", null, at(4 * DAY))],
        assignee: ME, status: "waiting_on_contact",
    }, [
        say(1, NEWS, "Do you ship to Canada?", at(4 * DAY), { sourceId: "june-mail" }),
        say(2, ME, "We do, from next month. I’ll send the rates once they are set.", at(4 * DAY - HOUR), { sourceId: "june-mail", delivered: [sent("june-mail", "RESEND")] }),
    ]);

    /* Launch: a private group that its Lemma answers line by line. */
    {
        const root = say(1, SAM, "Can we move the post to Tuesday?", at(3 * HOUR), { replyCount: 3, lastReplyAt: at(40 * MIN) });
        const lines = [
            root,
            say(2, LAUNCH_AGENT, "Tuesday works. I asked Ada to pick the day, since it moves the press list too.", at(3 * HOUR - 2 * MIN), { onBehalfOf: SAM, replyToId: root.id }),
            say(3, LAUNCH_AGENT, "Post the launch note to the press list now?", at(2 * HOUR), {
                onBehalfOf: SAM,
                interaction: { kind: "APPROVAL", state: "OPEN", question: { title: "Send the launch note to 48 people on the press list" }, awaits: ME, answer: null, answeredBy: null, answeredAt: null },
            }),
            say(4, ME, "Draft is in the folder, edits welcome.", at(90 * MIN), { editedAt: at(85 * MIN) }),
            say(5, ME, "And the screenshots are final.", at(88 * MIN)),
            say(6, LAUNCH_AGENT, "Here is what changed in the press list since Friday: 3 new names, 1 bounced address.", at(60 * MIN), { onBehalfOf: ME, visibility: "asker_only", canShare: true }),
            say(7, LAUNCH_AGENT, "The partner quote form is waiting on you.", at(50 * MIN), {
                interaction: { kind: "FORM", state: "OPEN", question: { run_id: "run-quotes", node_id: "quote" }, awaits: ME, answer: null, answeredBy: null, answeredAt: null },
            }),
            say(8, LAUNCH_AGENT, "Which channel gets the teaser first?", at(45 * MIN), {
                interaction: { kind: "ASK", state: "ANSWERED", question: {}, awaits: DEV, answer: { answers: { first: "Instagram" } }, answeredBy: DEV, answeredAt: at(30 * MIN) },
            }),
            say(9, DEV, "@Ada Lovelace screens are in the folder. Can you check the hero? https://acme.example/launch", at(5 * MIN), { parts: [mention(ME), { type: "link", url: "https://acme.example/launch", label: "Launch page" }] }),
        ];
        const replies = [
            say(10, DEV, "Tuesday is fine for the screens.", at(2 * HOUR + 30 * MIN), { threadRootId: root.id }),
            say(11, ME, "Then Tuesday, 9am.", at(2 * HOUR), { threadRootId: root.id }),
            say(12, LAUNCH_AGENT, "Moved. The press list goes out Tuesday at 9:00.", at(40 * MIN), { threadRootId: root.id, onBehalfOf: ME }),
        ];
        held.push({
            podId: LAUNCH.id,
            channel: channelOf("launch-week", "GROUP", {
                title: "launch-week", topic: "The October launch", visibility: "PRIVATE",
                members: [member(ME), member(SAM, "owner"), member(DEV), seat(LAUNCH_AGENT, "EVERY_LINE"), seat(RESEARCHER)],
                unread: { lines: 3, mentions: 1, waiting: 2, readSeq: 6 },
                me: { joined: true, notify: "ALL", notifyVia: ["SLACK"], mutedUntil: null, readSeq: 6, role: "member", canManage: true },
            }, now),
            lines,
            replies: new Map([[root.id, replies]]),
        });
    }

    /* Launch: a Telegram group. */
    {
        const tg = "press-tg";
        held.push({
            podId: LAUNCH.id,
            channel: channelOf("press-friends", "GROUP", {
                title: "Press friends", sources: [{ platform: "TELEGRAM", title: "Press friends" }],
                places: [place(tg, "TELEGRAM", "CHAT", "Press friends", at(2 * DAY))],
                members: [member(ME, "owner"), member(SAM), seat(LAUNCH_AGENT)],
                me: { joined: true, notify: "MENTIONS", notifyVia: null, mutedUntil: iso(-2 * HOUR, now), readSeq: 2, role: "owner", canManage: true },
            }, now),
            lines: [
                say(1, { kind: "CONTACT", id: "contact-ines", name: "Inès (Le Monde)" }, "@launch_acme_bot when does the embargo lift?", at(2 * DAY), { sourceId: tg }),
                say(2, LAUNCH_AGENT, "Tuesday at 9:00 Paris time. The kit is linked in the pinned message.", at(2 * DAY - MIN), { sourceId: tg }),
            ],
            replies: new Map(),
        });
    }

    for (const one of held) settle(one);
    return held;
}

/** The channel's own counts and last line, from its lines. */
function settle(one: Held): void {
    const all = [...one.lines, ...[...one.replies.values()].flat()];
    const top = one.lines.filter((line) => !line.deleted).at(-1) ?? one.lines.at(-1) ?? null;
    const lastSeq = all.reduce((most, line) => Math.max(most, line.seq), 0);
    one.channel.lastSeq = lastSeq;
    one.channel.lastLine = top ? { sender: top.sender, text: top.text ?? "", createdAt: top.createdAt } : null;
    one.channel.lastMessageAt = top?.createdAt ?? null;
}

/* ── history ───────────────────────────────────────────────────────── */

const OLDER_SUPPORT = [
    "Order 4471 follow-up", "Apology email wording", "Refund policy for sale items", "Late delivery to Leeds", "Warranty on floor lamps",
    "Bulk order from Northwind", "Invoice sent twice", "Store credit rules", "Damaged in transit, again", "Returns label not printing",
    "Shop chat greeting", "Customer survey questions", "Weekend cover", "Holiday shipping dates", "Price match request",
    "Lost parcel claim", "Spare parts for the brass lamp", "Gift wrap option", "Tone for complaint replies", "VAT on refunds",
    "Customer called twice", "Out of stock wording", "Replacement bulbs", "Chargeback from a card", "Delivery photo missing",
    "Thank-you notes", "Promo code not applying", "Order changed after dispatch", "Address typo fix", "Pickup in store",
];

function history(podId: string, now: number): ConversationRef[] {
    const pulse = samplePulse(now).find((one) => one.lemma.id === podId)?.conversations ?? [];
    const older: ConversationRef[] = podId === SUPPORT.id ? OLDER_SUPPORT.map((title, index) => ({
        id: "support-old-" + index,
        title,
        activityAt: iso(4 * DAY + index * 19 * HOUR, now),
        kind: "CHAT",
        boundTo: null,
        origin: { kind: "chat", label: "Chat" },
        agentId: null,
        status: "IDLE",
        waitingOnYou: false,
    })) : [];
    return [...pulse, ...older].sort((a, b) => (b.activityAt ?? "").localeCompare(a.activityAt ?? ""));
}

/* ── contacts ──────────────────────────────────────────────────────── */

function sampleContacts(now: number): Contact[] {
    return [
        { id: "contact-maria", name: "Maria Chen", createdAt: iso(40 * DAY, now), anonymous: false, handles: [{ kind: "PHONE", value: "447700900123", strength: "CHANNEL" }, { kind: "EMAIL", value: "maria@northwind.example", strength: "CODE" }] },
        { id: "contact-omar", name: "Omar Haddad", createdAt: iso(12 * DAY, now), anonymous: false, handles: [{ kind: "EMAIL", value: "omar@globex.example", strength: "CHANNEL" }] },
        { id: "contact-li", name: "Li Wei", createdAt: iso(3 * DAY, now), anonymous: false, handles: [{ kind: "WEB", value: "visitor-81", strength: "SESSION" }, { kind: "EMAIL", value: "li.wei@initech.example", strength: "CODE" }] },
        { id: "contact-june", name: "June Park", createdAt: iso(5 * DAY, now), anonymous: false, handles: [{ kind: "EMAIL", value: "june@park.example", strength: "CHANNEL" }] },
        { id: "contact-visitor", name: null, createdAt: iso(1 * DAY, now), anonymous: true, handles: [{ kind: "WEB", value: "visitor-90", strength: "SESSION" }] },
    ];
}

const WIDGETS: WebWidget[] = [
    { id: "widget-shop", name: "Shop chat", publicKey: "pk_sample_shop", allowedOrigins: ["https://shop.acme.example"], answer: "anyone", lookedAfterBy: null, embed: "<script src=\"https://lemma.work/widget.js\" data-key=\"pk_sample_shop\" async></script>", pageUrl: "https://lemma.work/chat/pk_sample_shop" },
];

const REACH: ContactReach = {
    tables: [
        { name: "tickets", contactOwned: true, perPerson: false, contactColumns: ["subject", "status"] },
        { name: "customers", contactOwned: false, perPerson: false, contactColumns: [] },
        { name: "replies", contactOwned: false, perPerson: true, contactColumns: [] },
    ],
    functions: [
        { name: "order_status", description: "Where an order is, by its number.", contactsInvoke: true },
        { name: "book_return", description: "Book a return pickup.", contactsInvoke: false },
    ],
};

/* ── the source ────────────────────────────────────────────────────── */

const SURFACES: Record<string, Surface[]> = {
    [SUPPORT.id]: [
        { id: "surface-whatsapp", platform: "WHATSAPP", name: "support-whatsapp", mine: true, agentName: SUPPORT.name, handle: "+44 20 7946 0000", active: true, status: "ACTIVE" },
        { id: "surface-resend", platform: "RESEND", name: "support-mail", mine: true, agentName: SUPPORT.name, handle: "support@acme.example", email: "support@acme.example", active: true, status: "ACTIVE" },
    ],
    [LAUNCH.id]: [
        { id: "surface-telegram", platform: "TELEGRAM", name: "launch-telegram", mine: true, agentName: LAUNCH.name, handle: "@launch_acme_bot", active: true, status: "ACTIVE" },
        { id: "surface-slack", platform: "SLACK", name: "launch-slack", mine: true, agentName: LAUNCH.name, handle: "launch", active: true, status: "ACTIVE" },
    ],
};

function agentsFor(podId: string): AgentRef[] {
    const lemma = SAMPLE_LEMMAS.find((one) => one.id === podId);
    const front: AgentRef = { id: "agent-" + podId, name: "pod_default", label: lemma?.name ?? "Lemma", front: true, blurb: lemma?.description ?? "" };
    if (podId === LAUNCH.id) return [front, { id: "agent-researcher", name: "researcher", label: "Researcher", front: false, blurb: "Finds sources and checks facts." }, { id: "agent-copy", name: "copy_editor", label: "Copy editor", front: false, blurb: "Tightens launch copy." }];
    if (podId === SUPPORT.id) return [front, { id: "agent-refunds", name: "refund_checker", label: "Refund checker", front: false, blurb: "Checks a refund against the policy." }];
    return [front];
}

function quote(): WaitingRun {
    return {
        active_wait: {
            node_id: "quote",
            payload: {
                input_schema: {
                    type: "object",
                    required: ["partner", "amount"],
                    properties: {
                        partner: { type: "string", title: "Partner", description: "Who the quote is for." },
                        amount: { type: "number", title: "Amount", description: "In pounds." },
                        rush: { type: "boolean", title: "Rush delivery" },
                    },
                },
                ui_schema: { "ui:order": ["partner", "amount", "rush"] },
            },
        },
    };
}

export function chatsSample(): ChatsSource {
    const now = Date.now();
    const store = build(now);
    const contacts = sampleContacts(now);
    const widgets = [...WIDGETS];
    const reach: ContactReach = { tables: REACH.tables.map((one) => ({ ...one })), functions: REACH.functions.map((one) => ({ ...one })) };
    const settings = new Map<string, SourceSettings>([
        ["care-wa", { id: "care-wa", channelId: "customer-care", platform: "WHATSAPP", title: "Acme × Northwind", surfaceName: "support-whatsapp", answersOutsiders: true, welcomesOutsiders: true, botAnswersOutsiders: true, canManage: true, owner: SAM, inviteLink: "https://chat.whatsapp.com/SampleInviteCare", pending: false, sharedExternally: false, publicReach: { files: [{ name: "Returns policy.md", path: "/policies/Returns policy.md" }], tables: ["tickets"], more: false } }],
        ["press-tg", { id: "press-tg", channelId: "press-friends", platform: "TELEGRAM", title: "Press friends", surfaceName: "launch-telegram", answersOutsiders: false, welcomesOutsiders: false, botAnswersOutsiders: true, canManage: true, owner: ME, inviteLink: null, pending: false, sharedExternally: false, publicReach: { files: [], tables: [], more: false } }],
    ]);
    const bots = new Map<string, BotAnswer>([["support-whatsapp", { answer: "known", own: true }], ["support-mail", { answer: "anyone", own: true }], ["launch-telegram", { answer: "off", own: false }]]);
    let autoAck: string | null = "Thanks — someone from Support will reply.";
    let made = 0;
    const answered = new Set<string>();

    const find = (podId: string, channelId: string): Held => {
        const one = store.find((held) => held.podId === podId && held.channel.id === channelId);
        if (!one) throw new Error("This chat is not here any more, or you are not in it.");
        return one;
    };
    const copy = (one: Held): ChannelDetail => ({ ...one.channel, members: [...one.channel.members], places: [...one.channel.places], me: { ...one.channel.me } });
    const listed = (one: Held): Channel => {
        const { places: _places, members: _members, me, ...rest } = copy(one);
        return { ...rest, joined: me.joined };
    };
    const lineAt = (one: Held, lineId: string): Line | null => {
        const top = one.lines.find((line) => line.id === lineId);
        if (top) return top;
        for (const replies of one.replies.values()) {
            const reply = replies.find((line) => line.id === lineId);
            if (reply) return reply;
        }
        return null;
    };
    const replace = (one: Held, line: Line) => {
        one.lines = one.lines.map((each) => (each.id === line.id ? line : each));
        for (const [root, replies] of one.replies) one.replies.set(root, replies.map((each) => (each.id === line.id ? line : each)));
    };
    const nextSeq = (one: Held) => one.channel.lastSeq + 1;
    const unreadOf = (one: Held) => {
        const readSeq = one.channel.me.readSeq;
        const all = [...one.lines, ...[...one.replies.values()].flat()];
        const fresh = all.filter((line) => line.seq > readSeq && !(line.sender.kind === "MEMBER" && line.sender.id === ME.id));
        const mentions = fresh.filter((line) => line.parts.some((part) => part.type === "mention" && part.id === ME.id)).length;
        const waiting = all.filter((line) => line.interaction && (line.interaction.state === "OPEN" || line.interaction.state === "ANSWERING") && line.interaction.awaits?.id === ME.id).length;
        one.channel.unread = { lines: one.lines.filter((line) => line.seq > readSeq && !(line.sender.kind === "MEMBER" && line.sender.id === ME.id)).length, mentions, waiting, readSeq };
    };
    for (const one of store) unreadOf(one);

    const matches = (one: Held, filter: Parameters<ChatsSource["list"]>[1]) => {
        const channel = one.channel;
        if (filter.kind && channel.kind !== filter.kind) return false;
        if (channel.archived !== Boolean(filter.archived)) return false;
        if (filter.assignee === "me" && channel.assignee?.id !== ME.id) return false;
        if (filter.unassigned && channel.assignee) return false;
        if (filter.status && channel.status !== filter.status) return false;
        if (filter.needsPerson && !(channel.kind === "CONTACT" && !channel.assignee && channel.status !== "closed" && (channel.attention || !channel.agentsAnswer))) return false;
        if (filter.contactId && channel.person?.id !== filter.contactId) return false;
        if (filter.q && !(channel.title ?? "").toLowerCase().includes(filter.q.toLowerCase())) return false;
        return channel.me.joined || channel.visibility === "PUBLIC" || channel.kind === "CONTACT";
    };

    const post = (podId: string, channelId: string, body: PostBody): Line => {
        const one = find(podId, channelId);
        if (one.channel.archived) throw new Error("This chat is archived. Bring it back to write in it.");
        const already = body.clientMessageId ? [...one.lines, ...[...one.replies.values()].flat()].find((line) => line.clientMessageId === body.clientMessageId) : null;
        if (already) return already;
        const live = one.channel.places.filter((each) => !each.pending);
        const to = body.membersOnly ? null : live.find((each) => each.id === body.sourceId) ?? live[0] ?? null;
        const parts: Part[] = [
            ...(body.mentions ?? []).map((who): Part => ({ type: "mention", kind: who.kind, id: who.id, name: PEOPLE.find((person) => person.userId === who.id)?.name ?? agentsFor(podId).find((agent) => agent.id === who.id)?.label ?? null })),
            ...(body.files ?? []).map((file): Part => ({ ...file, fileId: "file-" + (++made) })),
        ];
        const line = say(nextSeq(one), ME, body.text, new Date().toISOString(), {
            threadRootId: body.threadRootId ?? null,
            membersOnly: Boolean(body.membersOnly),
            clientMessageId: body.clientMessageId ?? null,
            parts,
            delivered: to && one.channel.kind !== "DIRECT" ? [sent(to.id, to.platform)] : null,
        });
        if (line.threadRootId) {
            const replies = one.replies.get(line.threadRootId) ?? [];
            one.replies.set(line.threadRootId, [...replies, line]);
            const root = one.lines.find((each) => each.id === line.threadRootId);
            if (root) replace(one, { ...root, replyCount: root.replyCount + 1, lastReplyAt: line.createdAt });
        } else {
            one.lines = [...one.lines, line];
        }
        one.channel.me.readSeq = line.seq;
        settle(one);
        unreadOf(one);
        return line;
    };

    const inbox = (podId: string): Inbox => {
        const items: WaitingQuestion[] = [];
        const mentioned: MentionItem[] = [];
        for (const one of store.filter((held) => held.podId === podId)) {
            const all = [...one.lines, ...[...one.replies.values()].flat()];
            for (const line of all) {
                const asks = line.interaction;
                if (asks && (asks.state === "OPEN" || asks.state === "ANSWERING") && asks.awaits?.id === ME.id && !answered.has(line.id)) {
                    items.push({ id: line.id, channelId: one.channel.id, channelTitle: one.channel.title, channelKind: one.channel.kind, kind: asks.kind, text: line.text ?? "", from: line.sender.name ?? "Someone", askedAt: line.createdAt, question: asks.question, threadRootId: line.threadRootId });
                }
                const names = line.parts.some((part) => part.type === "mention" && part.id === ME.id);
                if (names && line.seq > one.channel.me.readSeq) {
                    mentioned.push({ id: line.id, seq: line.seq, channelId: one.channel.id, channelTitle: one.channel.title, channelKind: one.channel.kind, from: line.sender.name ?? "Someone", text: line.text ?? "", at: line.createdAt, threadRootId: line.threadRootId });
                }
            }
        }
        const channels = store.filter((held) => held.podId === podId && held.channel.unread.lines > 0).length;
        return { channels, mentions: mentioned.length, waiting: items.length, items, mentioned };
    };

    return {
        async list(podId, filter) {
            const items = store.filter((one) => one.podId === podId && matches(one, filter)).map(listed)
                .sort((a, b) => (b.lastMessageAt ?? b.createdAt).localeCompare(a.lastMessageAt ?? a.createdAt));
            return { items, nextBefore: null };
        },
        async get(podId, channelId) {
            return copy(find(podId, channelId));
        },
        async lines(podId, channelId, beforeSeq) {
            const one = find(podId, channelId);
            const older = beforeSeq === undefined ? one.lines : one.lines.filter((line) => line.seq < beforeSeq);
            const page = older.slice(-6);
            const rest = older.length - page.length;
            return { items: page.map((line) => ({ ...line })), nextBefore: rest > 0 ? page[0].seq : null };
        },
        async thread(podId, channelId, rootId) {
            return { items: [...(find(podId, channelId).replies.get(rootId) ?? [])], nextAfter: null };
        },
        async post(podId, channelId, body) {
            return post(podId, channelId, body);
        },
        async edit(podId, channelId, lineId, text, parts) {
            const one = find(podId, channelId);
            const was = lineAt(one, lineId);
            if (!was) throw new Error("That message is not here any more.");
            const line = { ...was, text, editedAt: new Date().toISOString(), parts: parts ? [...parts] : was.parts };
            replace(one, line);
            settle(one);
            return line;
        },
        async remove(podId, channelId, lineId) {
            const one = find(podId, channelId);
            const was = lineAt(one, lineId);
            if (!was) return;
            replace(one, { ...was, deleted: true, text: null, parts: [], removedBy: was.sender.id === ME.id ? null : ME, canShare: false, delivered: null });
            settle(one);
        },
        async share(podId, channelId, lineId) {
            const one = find(podId, channelId);
            const was = lineAt(one, lineId);
            if (!was) throw new Error("That message is not here any more.");
            const line = { ...was, visibility: "channel" as const, canShare: false };
            replace(one, line);
            return line;
        },
        async markRead(podId, channelId, seq) {
            const one = find(podId, channelId);
            one.channel.me.readSeq = Math.max(one.channel.me.readSeq, seq);
            unreadOf(one);
        },
        async create(podId, body) {
            const id = "sample-group-" + (++made);
            const held: Held = {
                podId,
                channel: channelOf(id, "GROUP", {
                    title: body.title, topic: body.topic, visibility: body.visibility, createdAt: new Date().toISOString(),
                    members: [member(ME, "owner"), ...body.members.map((userId) => member({ kind: "MEMBER", id: userId, name: PEOPLE.find((one) => one.userId === userId)?.name ?? null })),
                        ...(body.agents ?? []).map((agent) => seat({ kind: "AGENT", id: agent.id, name: agentsFor(podId).find((one) => one.id === agent.id)?.label ?? null }, agent.answers))],
                    me: { joined: true, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 0, role: "owner", canManage: true },
                }, Date.now()),
                lines: [],
                replies: new Map(),
            };
            store.unshift(held);
            return copy(held);
        },
        async update(podId, channelId, change: ChannelChange) {
            const one = find(podId, channelId);
            const { assignee, ...rest } = change;
            Object.assign(one.channel, Object.fromEntries(Object.entries(rest).filter(([, value]) => value !== undefined)));
            if (assignee !== undefined) {
                const userId = assignee === "me" ? ME.id : assignee;
                const person = PEOPLE.find((one) => one.userId === userId);
                one.channel.assignee = person ? { kind: "MEMBER", id: person.userId, name: person.name } : null;
                if (person) one.channel.attention = null;
            }
            return copy(one);
        },
        async destroy(podId, channelId) {
            const at = store.findIndex((one) => one.podId === podId && one.channel.id === channelId);
            if (at >= 0) store.splice(at, 1);
        },
        async addMembers(podId, channelId, body) {
            const one = find(podId, channelId);
            for (const userId of body.people ?? []) {
                const person = PEOPLE.find((each) => each.userId === userId);
                if (person) one.channel.members.push(member({ kind: "MEMBER", id: userId, name: person.name }));
            }
            for (const agent of body.agents ?? []) {
                one.channel.members.push(seat({ kind: "AGENT", id: agent.id, name: agentsFor(podId).find((each) => each.id === agent.id)?.label ?? null }, agent.answers));
            }
            return copy(one);
        },
        async updateMember(podId, channelId, who, change) {
            const one = find(podId, channelId);
            one.channel.members = one.channel.members.map((each) => each.person.kind === who.kind && each.person.id === who.id
                ? { ...each, ...(change.role ? { role: change.role } : {}), ...(change.answers ? { answers: change.answers } : {}) }
                : each);
        },
        async removeMember(podId, channelId, who) {
            const one = find(podId, channelId);
            one.channel.members = one.channel.members.filter((each) => !(each.person.kind === who.kind && each.person.id === who.id));
        },
        async join(podId, channelId) {
            const one = find(podId, channelId);
            one.channel.me = { ...one.channel.me, joined: true };
            one.channel.joined = true;
            if (!one.channel.members.some((each) => each.person.id === ME.id)) one.channel.members.push(member(ME));
            return copy(one);
        },
        async leave(podId, channelId) {
            const one = find(podId, channelId);
            one.channel.me = { ...one.channel.me, joined: false };
            one.channel.joined = false;
            one.channel.members = one.channel.members.filter((each) => each.person.id !== ME.id);
        },
        async setMine(podId, channelId, change: MineChange) {
            const one = find(podId, channelId);
            one.channel.me = {
                ...one.channel.me,
                ...(change.notify !== undefined ? { notify: change.notify } : {}),
                ...(change.notifyVia !== undefined ? { notifyVia: change.notifyVia } : {}),
                ...(change.mutedUntil !== undefined ? { mutedUntil: change.mutedUntil } : {}),
            };
            return copy(one);
        },
        async setAgentAnswers(podId, channelId, on) {
            const one = find(podId, channelId);
            one.channel.agentsAnswer = on;
            if (on) one.channel.assignee = null;
            return copy(one);
        },
        async retryDelivery(podId, channelId, lineId, deliveryId) {
            const one = find(podId, channelId);
            const was = lineAt(one, lineId);
            if (!was) return;
            replace(one, { ...was, delivered: (was.delivered ?? []).map((each) => (each.id === deliveryId ? { ...each, status: "SENT" as const, reason: null } : each)) });
        },
        typists(podId, channelId) {
            const one = store.find((held) => held.podId === podId && held.channel.id === channelId);
            if (!one || channelId !== "launch-week") return [];
            const root = one.lines.find((line) => line.replyCount > 0);
            return [{ who: DEV, threadRootId: null }, ...(root ? [{ who: SAM, threadRootId: root.id }] : [])];
        },
        async attach(_podId, channelId, file, line) {
            return { type: "file", path: "/me/channels/" + channelId + "/" + line.clientMessageId + "/" + line.name, name: line.name, mimeType: file.type || null, size: file.size, fileId: null };
        },
        async lineFile(_podId, _channelId, _lineId, file) {
            /* The pictures and the recording the sample chats share, drawn
               for the app; read only here, so tests never load them. */
            const path = SAMPLE_LINE_FILES[file.fileId];
            if (path) {
                const { sampleMediaUrl } = await import("../chat/sample-media");
                const shown = await sampleMediaUrl(path);
                if (shown) return shown;
            }
            if (/\.(png|jpe?g|gif|webp)$/i.test(file.name)) return "data:image/png;base64," + SAMPLE_PNG;
            throw new Error("Sample data has no file to open. Sign in to open your own.");
        },
        async answer(podId, channelId, lineId, given) {
            const one = find(podId, channelId);
            const was = lineAt(one, lineId);
            if (!was?.interaction) throw new Error("That question is not waiting any more.");
            answered.add(lineId);
            replace(one, { ...was, interaction: { ...was.interaction, state: "ANSWERED", answer: given, answeredBy: ME, answeredAt: new Date().toISOString() } });
            unreadOf(one);
        },
        async inbox(podId) {
            return inbox(podId);
        },
        async search(podId, q) {
            const words = q.toLowerCase().split(/\s+/).filter(Boolean);
            const hits: SearchHit[] = [];
            for (const one of store.filter((held) => held.podId === podId)) {
                for (const line of [...one.lines, ...[...one.replies.values()].flat()]) {
                    const text = (line.text ?? "").toLowerCase();
                    if (!line.text || !words.every((word) => text.includes(word))) continue;
                    hits.push({ line, channel: { id: one.channel.id, kind: one.channel.kind, title: one.channel.title, person: one.channel.person }, snippet: null });
                }
            }
            hits.sort((a, b) => b.line.createdAt.localeCompare(a.line.createdAt));
            return { items: hits, nextBefore: null };
        },
        async online() {
            return ["sam", "priya"];
        },

        async source(_podId, _channelId, sourceId) {
            const held = settings.get(sourceId);
            if (!held) throw new Error("This group has no settings to show.");
            return { ...held };
        },
        async updateSource(_podId, _channelId, sourceId, change) {
            const held = settings.get(sourceId);
            if (!held) throw new Error("This group has no settings to show.");
            const next = {
                ...held,
                ...(change.answers_outsiders !== undefined ? { answersOutsiders: change.answers_outsiders } : {}),
                ...(change.owner === "me" ? { owner: ME } : {}),
            };
            next.welcomesOutsiders = next.answersOutsiders && next.owner !== null && next.botAnswersOutsiders;
            settings.set(sourceId, next);
            return { ...next };
        },
        async startWhatsAppGroup(podId, body) {
            const id = "sample-wa-" + (++made);
            const source = "wa-src-" + made;
            store.unshift({
                podId,
                channel: channelOf(id, "GROUP", {
                    title: body.title, sources: [{ platform: "WHATSAPP", title: body.title }], createdAt: new Date().toISOString(),
                    places: [{ ...place(source, "WHATSAPP", "CHAT", body.title, new Date().toISOString()), lastInboundAt: null }],
                    members: [member(ME, "owner"), seat(agentOf(podId, SAMPLE_LEMMAS.find((one) => one.id === podId)?.name ?? "Lemma"))],
                    me: { joined: true, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 0, role: "owner", canManage: true },
                }, Date.now()),
                lines: [],
                replies: new Map(),
            });
            const made_: SourceSettings = { id: source, channelId: id, platform: "WHATSAPP", title: body.title, surfaceName: body.surfaceName, answersOutsiders: body.answersOutsiders, welcomesOutsiders: body.answersOutsiders, botAnswersOutsiders: true, canManage: true, owner: ME, inviteLink: "https://chat.whatsapp.com/Sample" + made, pending: false, sharedExternally: false, publicReach: { files: [], tables: [], more: false } };
            settings.set(source, made_);
            return { ...made_ };
        },
        async telegramLink() {
            return { url: "https://t.me/launch_acme_bot?startgroup=sample", expiresAt: new Date(Date.now() + HOUR).toISOString() };
        },
        async surfaces(podId) {
            return SURFACES[podId] ?? [];
        },

        async agents(podId) {
            return agentsFor(podId);
        },
        async people() {
            return PEOPLE;
        },
        async abilities() {
            return null;
        },
        async conversations(podId, cursor, search) {
            const all = history(podId, now).filter((one) => !search || one.title.toLowerCase().includes(search.toLowerCase()));
            const from = cursor ? Number(cursor) : 0;
            const page = all.slice(from, from + 20);
            return { items: page, next: from + 20 < all.length ? String(from + 20) : null };
        },
        async workflowRun(_podId, runId) {
            return runId === "run-quotes" && !answered.has("run-quotes") ? quote() : { active_wait: null };
        },
        async submitForm(_podId, runId) {
            answered.add(runId);
            for (const one of store) {
                for (const line of one.lines) {
                    if (line.interaction?.kind === "FORM" && line.interaction.question.run_id === runId) {
                        answered.add(line.id);
                        replace(one, { ...line, interaction: { ...line.interaction, state: "ANSWERED", answeredBy: ME, answeredAt: new Date().toISOString() } });
                        unreadOf(one);
                    }
                }
            }
        },

        async contacts(podId) {
            return podId === SUPPORT.id ? contacts.map((one) => ({ ...one })) : [];
        },
        async renameContact(_podId, contactId, name) {
            const one = contacts.find((each) => each.id === contactId);
            if (!one) throw new Error("They are not one of this Lemma’s contacts any more.");
            one.name = name;
            return { ...one };
        },
        async forgetContact(_podId, contactId) {
            const at = contacts.findIndex((each) => each.id === contactId);
            if (at >= 0) contacts.splice(at, 1);
        },
        async exportContact(_podId, contactId) {
            const one = contacts.find((each) => each.id === contactId);
            return JSON.stringify({ contact: one ?? null, conversations: [], note: "Sample data" }, null, 2);
        },
        async followUp(podId, contactId, message, handle, clientMessageId) {
            const held = store.find((one) => one.podId === podId && one.channel.person?.id === contactId);
            if (!held) throw new Error("They have no chat yet: it starts when they first write.");
            const line = post(podId, held.channel.id, { text: message, clientMessageId });
            return { channelId: held.channel.id, lineId: line.id, platform: handle.kind === "EMAIL" ? "RESEND" : handle.kind === "PHONE" ? "WHATSAPP" : handle.kind, status: "SENT", reason: null };
        },
        async widgets() {
            return widgets.map((one) => ({ ...one }));
        },
        async createWidget(_podId, draft) {
            const key = "pk_sample_" + (++made);
            const one: WebWidget = { id: "widget-" + made, name: draft.name.trim(), publicKey: key, allowedOrigins: draft.origins.split(/[\s,]+/).filter(Boolean), answer: draft.answer, lookedAfterBy: null, embed: "<script src=\"https://lemma.work/widget.js\" data-key=\"" + key + "\" async></script>", pageUrl: "https://lemma.work/chat/" + key };
            widgets.push(one);
            return { ...one, signingSecret: "whsec_sample_" + made };
        },
        async updateWidget(_podId, widgetId, change) {
            const one = widgets.find((each) => each.id === widgetId);
            if (!one) throw new Error("That web chat is gone.");
            if (change.answer) one.answer = change.answer;
            return { ...one };
        },
        async reissueWidget() {
            return "whsec_sample_" + (++made);
        },
        async deleteWidget(_podId, widgetId) {
            const at = widgets.findIndex((each) => each.id === widgetId);
            if (at >= 0) widgets.splice(at, 1);
        },
        async reach(podId) {
            return podId === SUPPORT.id ? { tables: reach.tables.map((one) => ({ ...one })), functions: reach.functions.map((one) => ({ ...one })) } : { tables: [], functions: [] };
        },
        async setTableContactOwned(_podId, table, on, columns) {
            const one = reach.tables.find((each) => each.name === table);
            if (one) { one.contactOwned = on; one.contactColumns = on ? columns ?? [] : []; }
        },
        async setFunctionContactsInvoke(_podId, fn, on) {
            const one = reach.functions.find((each) => each.name === fn);
            if (one) one.contactsInvoke = on;
        },
        async shareableColumns(_podId, table) {
            return table === "customers" ? ["name", "email", "plan", "since"] : ["subject", "status", "opened"];
        },
        async autoAcknowledge() {
            return autoAck;
        },
        async setAutoAcknowledge(_podId, said) {
            autoAck = said;
        },
        async botAnswer(_podId, surfaceName) {
            return bots.get(surfaceName) ?? { answer: "off", own: true };
        },
        async setBotAnswer(_podId, surfaceName, answer) {
            bots.set(surfaceName, { answer, own: true });
        },
    };
}
