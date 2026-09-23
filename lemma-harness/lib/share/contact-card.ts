/**
 * An agent as a saveable contact.
 *
 * The end of "chat where you already chat" is that the agent stops being
 * something you open and becomes a row in the address book, next to people. So
 * this hands out a vCard: name, face, and the handles it answers on — saved to
 * a phone, and from then on the agent is reachable the way a person is.
 *
 * Everything on the card rides in the link, exactly as `share-link.ts` does it,
 * and for the same reason: the page renders for a stranger who will never hold
 * a session, and `ResourceVisibility.PUBLIC` is documented as *never anonymous*.
 * That is not a workaround here — a Telegram handle and a WhatsApp number are
 * public addresses by construction. Handing them to strangers is the point of
 * the card, so a link that carries them discloses nothing its sender did not
 * mean to send.
 *
 * The cost is that a card is frozen when it is minted. A rotated bot token
 * changes the handle, and the cards already saved in other people's phones keep
 * the old one — nothing can reach in and correct them. `UID` and `REV` are what
 * make that survivable: re-sharing the link updates the saved contact in place
 * instead of adding a second one.
 *
 * This module is the spec both renderers draw from — the landing page and
 * `/api/contact-card` — so the rows someone reads can never disagree with the
 * file they save, the same arrangement `social-card.ts` has with its renderer.
 */

/**
 * Query keys, kept to two characters.
 *
 * A contact card is the one share link that gets printed as a QR code, and QR
 * density is driven by payload length — long keys cost scan reliability at the
 * far end of a room.
 */
export const CONTACT_PARAMS = {
    /** The identity the face is drawn from — the agent's id. See `agentIdentitySeed`. */
    seed: 'sd',
    /** Raw `icon_url`: an uploaded picture, a typed emoji, or a variant sentinel. */
    icon: 'ic',
    org: 'o',
    note: 'd',
    telegram: 'tg',
    whatsapp: 'wa',
    email: 'em',
} as const;

export interface ContactCardSpec {
    name: string;
    seed: string;
    icon?: string | null;
    org?: string | null;
    note?: string | null;
    /** Normalised to a leading `@`. */
    telegram?: string | null;
    /** Normalised to E.164. */
    whatsapp?: string | null;
    email?: string | null;
}

/** One row on the card: somewhere a person can actually start a conversation. */
export interface ContactChannel {
    key: 'telegram' | 'whatsapp' | 'email';
    /**
     * The surface platform behind the row, so the page can take its logo and
     * label from the surface registry instead of keeping a second copy of both.
     */
    platform: 'TELEGRAM' | 'WHATSAPP' | 'RESEND';
    label: string;
    /** What to print — `@triage_bot`, `+1 555…`, an address. */
    value: string;
    /** Where the button goes. Always set; a row with no link is not a row. */
    href: string;
}

/*
 * Every value below arrives from a URL that anyone can type, and lands in a file
 * someone saves to their phone. The formats are narrow on purpose: a handle is
 * the character set Telegram allows and nothing else, a number is digits. What
 * fails these is dropped rather than repaired — a card that silently omits a row
 * is honest, a card that prints an attacker's text under the agent's name is not.
 */
const TELEGRAM_HANDLE = /^@?[A-Za-z0-9_]{4,32}$/;
const E164 = /^\+?[1-9]\d{6,14}$/;
const EMAIL = /^[^\s@,;:<>"'\\]+@[^\s@,;:<>"'\\]+\.[^\s@,;:<>"'\\]{2,}$/;

/** Long enough for a sentence about the agent, short enough to keep the QR scannable. */
const MAX_NOTE = 200;
const MAX_NAME = 120;
const MAX_ORG = 120;

function clean(value: string | null | undefined, max: number): string | null {
    // Control characters go first and unconditionally. A newline reaching the
    // vCard writer is the whole injection surface — CRLF is the property
    // separator, so one smuggled through a display name would let a link append
    // properties of its own to a file somebody is about to trust.
    const trimmed = value
        ?.replace(/[\u0000-\u001f\u007f]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    return trimmed ? trimmed.slice(0, max) : null;
}

export function normalizeTelegram(value: string | null | undefined): string | null {
    const trimmed = clean(value, 33);
    if (!trimmed || !TELEGRAM_HANDLE.test(trimmed)) return null;
    return `@${trimmed.replace(/^@/, '')}`;
}

export function normalizeWhatsApp(value: string | null | undefined): string | null {
    // Minted from a display phone number, which arrives spaced and bracketed the
    // way the provider prints it; E.164 is what both `wa.me` and a phone's dialer
    // want back.
    const digits = clean(value, 24)?.replace(/[^\d+]/g, '');
    if (!digits) return null;
    const e164 = `+${digits.replace(/\+/g, '')}`;
    return E164.test(e164) ? e164 : null;
}

export function normalizeEmail(value: string | null | undefined): string | null {
    const trimmed = clean(value, 254)?.toLowerCase();
    if (!trimmed || !EMAIL.test(trimmed)) return null;
    return trimmed;
}

/** Read a card off a link, or null when the link names nothing to draw. */
export function readContactCardSpec(
    query: URLSearchParams,
    name: string | null,
): ContactCardSpec | null {
    const displayName = clean(name, MAX_NAME);
    if (!displayName) return null;

    const spec: ContactCardSpec = {
        name: displayName,
        // The face falls back to the name only when the link carries no seed —
        // the same id-or-name rule the workspace draws by.
        seed: clean(query.get(CONTACT_PARAMS.seed), MAX_NAME) || displayName,
        icon: clean(query.get(CONTACT_PARAMS.icon), 512),
        org: clean(query.get(CONTACT_PARAMS.org), MAX_ORG),
        note: clean(query.get(CONTACT_PARAMS.note), MAX_NOTE),
        telegram: normalizeTelegram(query.get(CONTACT_PARAMS.telegram)),
        whatsapp: normalizeWhatsApp(query.get(CONTACT_PARAMS.whatsapp)),
        email: normalizeEmail(query.get(CONTACT_PARAMS.email)),
    };
    return spec;
}

/** The card's params, for building a link. Empty values are left out entirely. */
export function contactCardParams(spec: ContactCardSpec): URLSearchParams {
    const params = new URLSearchParams();
    const entries: Array<[string, string | null | undefined]> = [
        [CONTACT_PARAMS.seed, spec.seed === spec.name ? null : spec.seed],
        [CONTACT_PARAMS.icon, spec.icon],
        [CONTACT_PARAMS.org, spec.org],
        [CONTACT_PARAMS.note, spec.note],
        [CONTACT_PARAMS.telegram, normalizeTelegram(spec.telegram)],
        [CONTACT_PARAMS.whatsapp, normalizeWhatsApp(spec.whatsapp)],
        [CONTACT_PARAMS.email, normalizeEmail(spec.email)],
    ];
    for (const [key, value] of entries) {
        if (value) params.set(key, value);
    }
    return params;
}

/**
 * The rows to show, in the order they are worth offering.
 *
 * Telegram first: it is the one platform where an agent can hold an identity
 * genuinely its own — its own bot, its own handle, at no cost — so it is the
 * row most likely to be the agent rather than the pod it lives in.
 */
export function contactChannels(spec: ContactCardSpec): ContactChannel[] {
    const channels: ContactChannel[] = [];
    if (spec.telegram) {
        channels.push({
            key: 'telegram',
            platform: 'TELEGRAM',
            label: 'Telegram',
            value: spec.telegram,
            href: `https://t.me/${spec.telegram.replace(/^@/, '')}`,
        });
    }
    if (spec.whatsapp) {
        channels.push({
            key: 'whatsapp',
            platform: 'WHATSAPP',
            label: 'WhatsApp',
            value: spec.whatsapp,
            href: `https://wa.me/${spec.whatsapp.replace(/\D/g, '')}`,
        });
    }
    if (spec.email) {
        channels.push({
            key: 'email',
            platform: 'RESEND',
            label: 'Email',
            value: spec.email,
            href: `mailto:${spec.email}`,
        });
    }
    return channels;
}

/**
 * Where the `.vcf` for a card page lives.
 *
 * The two paths mirror each other segment for segment — `/s/contact/pod/…` and
 * `/api/contact-card/pod/…` — so the page can point at its own file without
 * either of them knowing how the other is routed.
 */
export function contactCardDownloadPath(
    segments: string[] | undefined,
    query: URLSearchParams,
): string {
    const path = (segments ?? []).filter(Boolean).map(encodeURIComponent).join('/');
    const search = query.toString();
    return `/api/contact-card/${path}${search ? `?${search}` : ''}`;
}

/** A filename someone can find again in their downloads. */
export function contactCardFilename(spec: ContactCardSpec): string {
    const slug = spec.name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');
    return `${slug || 'agent'}.vcf`;
}

/**
 * Escape a vCard TEXT value (RFC 2426 §2.4.2).
 *
 * `clean` has already taken the newlines, so this is the second of two passes
 * rather than the only one — the separators a value could otherwise impersonate
 * are `;` and `,`, and a literal backslash has to go first or it would escape
 * the escapes.
 */
function escapeText(value: string): string {
    return value
        .replace(/\\/g, '\\\\')
        .replace(/;/g, '\\;')
        .replace(/,/g, '\\,')
        .replace(/\r?\n/g, '\\n');
}

/**
 * Fold to 75 octets (RFC 2426 §2.6), which is not optional in practice.
 *
 * An embedded photo is a single property some tens of kilobytes long, and the
 * importers that reject an unfolded line do it by dropping the whole card — the
 * failure people report as "it saved with a blank name".
 *
 * Measured in octets, not characters, and never split mid-sequence: an agent
 * named in Devanagari folds at three bytes per glyph, and half a code point on
 * each side of the break is mojibake in someone's address book.
 */
function foldLine(line: string): string {
    const encoder = new TextEncoder();
    if (encoder.encode(line).length <= 75) return line;

    const parts: string[] = [];
    let current = '';
    let octets = 0;
    // Continuation lines start with a space, which costs one of the 75.
    const limit = () => (parts.length === 0 ? 75 : 74);

    for (const char of line) {
        const width = encoder.encode(char).length;
        if (octets + width > limit()) {
            parts.push(current);
            current = '';
            octets = 0;
        }
        current += char;
        octets += width;
    }
    if (current) parts.push(current);

    return parts.join('\r\n ');
}

export interface VCardPhoto {
    /** Base64, already encoded. */
    data: string;
    /** vCard 3.0 names the format bare — `PNG`, `JPEG`. */
    type: 'PNG' | 'JPEG';
}

/**
 * The saveable file.
 *
 * **Version 3.0, not 4.0.** 4.0 is the better spec and the worse choice here:
 * iOS, Android, Outlook and Google Contacts all read 3.0, and where they
 * disagree about 4.0 they disagree silently. The same conservatism picks
 * `TYPE=CELL` over a `TEL;VALUE=uri` and an embedded photo over a `URI` one.
 */
export function buildVCard(
    spec: ContactCardSpec,
    options: {
        photo?: VCardPhoto | null;
        url?: string | null;
        revision?: Date;
        /**
         * What this contact *is*, across every mint of it. Defaults to the seed,
         * which is the agent's own id and unique on its own.
         *
         * Passed separately because Lem breaks the assumption the seed encodes:
         * its seed is one constant shared by every pod, so keying on it would
         * make every pod's Lem the same contact — and saving a second one would
         * silently overwrite the first in the address book. Callers that know
         * the pod scope it; nothing else has to change.
         */
        uid?: string | null;
    } = {},
): string {
    const lines: string[] = ['BEGIN:VCARD', 'VERSION:3.0'];
    const name = escapeText(spec.name);

    // N is structured (family;given;…) and its separators are real semicolons,
    // so it is assembled from an escaped value rather than escaped as a whole.
    lines.push(`N:;${name};;;`);
    lines.push(`FN:${name}`);
    if (spec.org) lines.push(`ORG:${escapeText(spec.org)}`);
    lines.push('TITLE:Agent on Lemma');
    if (spec.note) lines.push(`NOTE:${escapeText(spec.note)}`);

    if (spec.whatsapp) lines.push(`TEL;TYPE=CELL:${escapeText(spec.whatsapp)}`);
    if (spec.email) lines.push(`EMAIL;TYPE=INTERNET:${escapeText(spec.email)}`);
    if (spec.telegram) {
        const handle = spec.telegram.replace(/^@/, '');
        // Two spellings of one fact: iOS renders `X-SOCIALPROFILE` as a tappable
        // row, everything else at least keeps the URL. Neither is universal, and
        // a contact that knows the handle but cannot show it is still worth more
        // than one that dropped it.
        lines.push(`URL;TYPE=Telegram:https://t.me/${handle}`);
        lines.push(`X-SOCIALPROFILE;TYPE=telegram:https://t.me/${handle}`);
    }
    if (options.url) lines.push(`URL:${escapeText(options.url)}`);

    if (options.photo) {
        lines.push(`PHOTO;ENCODING=b;TYPE=${options.photo.type}:${options.photo.data}`);
    }

    // Stable across every mint of this agent's card, so re-sharing an updated
    // link merges into the contact already saved rather than adding a twin.
    lines.push(`UID:urn:lemma:agent:${escapeText(options.uid || spec.seed)}`);
    lines.push(`REV:${formatRevision(options.revision ?? new Date())}`);
    lines.push('END:VCARD');

    // CRLF between properties and a trailing one at the end, both required.
    return `${lines.map(foldLine).join('\r\n')}\r\n`;
}

/** `REV` takes a basic-format UTC timestamp — no separators, `Z` suffix. */
function formatRevision(date: Date): string {
    return `${date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')}`;
}
