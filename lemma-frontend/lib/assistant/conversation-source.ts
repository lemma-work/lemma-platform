/**
 * Where a conversation came from, and who wrote each line of it.
 *
 * A pod's history mixes conversations somebody typed here with conversations
 * that arrived from Slack, Teams, WhatsApp, Telegram or a mailbox. Rendered
 * identically they are indistinguishable, and the user bubble is worse than
 * ambiguous: it is right-aligned in the reader's own voice, so a message a
 * field worker sent over WhatsApp reads as something the reader said.
 *
 * The backend has always known better. Every inbound surface message carries
 * `surface_platform`, the sender's name/email/phone and the platform's own event
 * metadata; the conversation carries the surface, the channel and the
 * `conversation_kind`. None of it was ever read here. This module is the whole
 * of the reading, kept pure so the rules are testable without rendering.
 *
 * What it deliberately does NOT do is imitate the platform. The Lemma transcript
 * is not the platform transcript — a group conversation is one person's slice of
 * a channel, tool calls and approval cards never existed on WhatsApp, and voice
 * notes arrive already transcribed — so a WhatsApp-shaped skin would be a claim
 * we cannot honour. What differentiates is *structure*, which the backend
 * already models as three shapes: a chat, a channel, and a mail thread.
 */

export type SurfacePlatformKey = 'SLACK' | 'TEAMS' | 'WHATSAPP' | 'TELEGRAM' | 'RESEND';

/** The three transcript shapes. `conversation_kind` on the wire, in our nouns. */
export type ConversationShape = 'dm' | 'channel' | 'mail';

interface PlatformBrand {
    label: string;
    /** A file under `public/connector-logos`, or null where we have no mark. */
    logo: string | null;
}

/**
 * `RESEND` is a transport, not a place. The settings panel names it, because an
 * admin picking between surfaces needs to know which one; a reader looking at a
 * conversation does not, and "Resend" would send them to look it up.
 */
const PLATFORM_BRAND: Record<SurfacePlatformKey, PlatformBrand> = {
    SLACK: { label: 'Slack', logo: '/connector-logos/slack.svg' },
    TEAMS: { label: 'Teams', logo: '/connector-logos/teams.svg' },
    WHATSAPP: { label: 'WhatsApp', logo: '/connector-logos/whatsapp.svg' },
    TELEGRAM: { label: 'Telegram', logo: '/connector-logos/telegram.svg' },
    RESEND: { label: 'Email', logo: null },
};

export interface ConversationSource {
    platform: SurfacePlatformKey;
    /** "WhatsApp", "Slack", "Email". */
    label: string;
    logo: string | null;
    shape: ConversationShape;
    /** Already `#`-prefixed. Null unless the shape is a channel. */
    channel: string | null;
    /** The surface row this arrived on, when the metadata names it. */
    surfaceId: string | null;
}

/** The human on the other end of one message. */
export interface MessageSender {
    name: string | null;
    email: string | null;
    phone: string | null;
    /** The one line worth printing: name, else email, else phone. */
    label: string | null;
}

/**
 * One message from the surrounding channel, fetched fresh for a run and stored
 * alongside the message it gave context to.
 */
export interface ChannelContextEntry {
    author: string | null;
    text: string;
    ts: string | null;
}

/** What this module reads. Structural, so both a `Conversation` and an
 *  `AssistantRenderableMessage` satisfy it without importing either. */
export interface HasMetadata {
    metadata?: unknown;
    message_metadata?: unknown;
}

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === 'object' && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
}

function text(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const trimmed = value.trim();
    return trimmed || null;
}

/**
 * The metadata bag carrying surface provenance.
 *
 * Both spellings are checked because the two sides of the wire disagree by era:
 * a conversation carries one `metadata`, a message can arrive with either, and
 * the SDK's own reader prefers `message_metadata`. Whichever actually names a
 * platform is the one to read — picking by precedence alone would read an empty
 * bag and conclude the conversation came from nowhere.
 */
function sourceRecord(subject: HasMetadata): Record<string, unknown> {
    const metadata = record(subject.metadata);
    if (text(metadata.surface_platform)) return metadata;
    const messageMetadata = record(subject.message_metadata);
    if (text(messageMetadata.surface_platform)) return messageMetadata;
    return metadata;
}

function platformOf(bag: Record<string, unknown>): SurfacePlatformKey | null {
    const raw = text(bag.surface_platform)?.toUpperCase();
    return raw && raw in PLATFORM_BRAND ? (raw as SurfacePlatformKey) : null;
}

/**
 * The shape of the thread, which decides how the transcript reads before the
 * platform's name means anything: a Slack DM and a Slack channel are not the
 * same thing to look at.
 *
 * `conversation_kind` is the backend's own answer and is trusted when present.
 * Rows written before it was stored on the message fall back to the platform,
 * which can only settle mail — claiming "channel" without being told is exactly
 * the kind of confident guess that puts two names on a conversation that had
 * eight people in it.
 */
function shapeOf(bag: Record<string, unknown>, platform: SurfacePlatformKey): ConversationShape {
    const kind = text(bag.conversation_kind)?.toUpperCase();
    if (kind === 'CHANNEL') return 'channel';
    if (kind === 'EMAIL') return 'mail';
    if (kind === 'DM') return 'dm';
    return platform === 'RESEND' ? 'mail' : 'dm';
}

/** Channel label for a channel-shaped conversation. Name when the surface's
 *  routes had one; otherwise nothing, because a raw `C07AB12CD` names no place
 *  a reader could recognise and reads as a bug. */
function channelOf(bag: Record<string, unknown>, shape: ConversationShape): string | null {
    if (shape !== 'channel') return null;
    const name = text(bag.channel_name);
    if (!name) return null;
    return name.startsWith('#') ? name : `#${name}`;
}

/**
 * Where this conversation or message came from, or null when it came from here.
 *
 * Null is the common case and the right answer for it: a conversation typed in
 * this app has no source to name, and labelling it "Web" would put a badge on
 * every row to say nothing.
 */
export function readSource(subject: HasMetadata | null | undefined): ConversationSource | null {
    if (!subject) return null;
    const bag = sourceRecord(subject);
    const platform = platformOf(bag);
    if (!platform) return null;

    const shape = shapeOf(bag, platform);
    const brand = PLATFORM_BRAND[platform];
    return {
        platform,
        label: brand.label,
        logo: brand.logo,
        shape,
        channel: channelOf(bag, shape),
        surfaceId: text(bag.surface_id),
    };
}

/**
 * The conversation's own source, read off the messages in it.
 *
 * The first message that names one wins, and the rest are not consulted: a
 * conversation belongs to exactly one surface thread, so a second answer would
 * mean the transcript had been mixed rather than that the source had changed.
 * Scanning rather than reading message zero is for a partially loaded history,
 * where the oldest message on screen may be a reply and carry no metadata.
 */
export function firstSource(
    subjects: readonly (HasMetadata | null | undefined)[],
): ConversationSource | null {
    for (const subject of subjects) {
        const source = readSource(subject);
        if (source) return source;
    }
    return null;
}

/** The person reading. Enough of them to recognise their own messages. */
export interface Viewer {
    email?: string | null;
    name?: string | null;
}

/** The human who sent a message. */
export function readSender(message: HasMetadata | null | undefined): MessageSender | null {
    if (!message) return null;
    const bag = sourceRecord(message);
    if (!platformOf(bag)) return null;

    const name = text(bag.sender_display_name);
    const email = text(bag.sender_email);
    const phone = text(bag.sender_phone);
    if (!name && !email && !phone) return null;

    return { name, email, phone, label: name ?? email ?? phone };
}

function same(left: string | null | undefined, right: string | null | undefined): boolean {
    if (!left || !right) return false;
    return left.trim().toLowerCase() === right.trim().toLowerCase();
}

/**
 * Whether this sender is the person reading the page.
 *
 * Worth asking because the usual answer is yes: a conversation is scoped to one
 * member, and the copy of it you are looking at is your own. Printing "Deepak ·
 * deepak@example.com" over Deepak's own message is not attribution, it is the
 * page telling him his name once per turn.
 */
export function senderIsViewer(sender: MessageSender, viewer: Viewer | null | undefined): boolean {
    if (!viewer) return false;
    return same(sender.email, viewer.email) || same(sender.name, viewer.name);
}

/**
 * The other human in this conversation, if there is one to name.
 *
 * Conversation-level rather than per-message on purpose: a surface conversation
 * holds exactly one person's messages — a channel gives each member their own —
 * so the sender is a fact about the conversation, and repeating it over every
 * bubble says the same thing as many times as you scroll.
 */
export function readCounterpart(
    subjects: readonly (HasMetadata | null | undefined)[],
    viewer: Viewer | null | undefined,
): MessageSender | null {
    for (const subject of subjects) {
        const sender = readSender(subject);
        if (!sender) continue;
        return senderIsViewer(sender, viewer) ? null : sender;
    }
    return null;
}

/**
 * The surrounding channel messages the run was given as background.
 *
 * Shown, and shown as separate from the conversation, because the alternative
 * is worse in both directions: hidden, a channel transcript reads as a
 * two-person exchange that never happened; merged in, messages nobody in this
 * conversation sent read as part of it.
 */
export function readChannelContext(message: HasMetadata | null | undefined): ChannelContextEntry[] {
    if (!message) return [];
    const raw = sourceRecord(message).channel_context;
    if (!Array.isArray(raw)) return [];

    return raw.flatMap((item) => {
        const entry = record(item);
        const body = text(entry.text);
        if (!body) return [];
        return [{ author: text(entry.author), text: body, ts: text(entry.ts) }];
    });
}

/** An email's subject line, which is the one thing a mail thread has that a
 *  chat does not — and the thing a bubble stream drops on the floor. */
export function readSubject(message: HasMetadata | null | undefined): string | null {
    if (!message) return null;
    const bag = sourceRecord(message);
    if (platformOf(bag) !== 'RESEND') return null;
    return text(bag.subject);
}

/**
 * How to introduce the conversation in one line: "WhatsApp", "Slack · #field-ops",
 * "Email". The place is appended only when we can name it.
 */
export function sourceHeadline(source: ConversationSource): string {
    return source.channel ? `${source.label} · ${source.channel}` : source.label;
}

/** Where the reader is, in the words of the platform they are looking at. Used
 *  under the headline, so "Slack · #field-ops" does not have to also explain
 *  that a channel is a channel. */
export function shapeDescription(source: ConversationSource): string {
    if (source.shape === 'mail') return 'Email thread';
    if (source.shape === 'channel') return source.channel ? 'Channel' : 'Channel message';
    return 'Direct message';
}
