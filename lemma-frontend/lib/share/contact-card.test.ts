import { describe, expect, it } from 'vitest';

import {
    buildVCard,
    contactCardFilename,
    contactCardParams,
    contactChannels,
    normalizeEmail,
    normalizeTelegram,
    normalizeWhatsApp,
    readContactCardSpec,
    type ContactCardSpec,
} from './contact-card';

const REVISION = new Date('2026-08-29T10:30:00.000Z');

function spec(overrides: Partial<ContactCardSpec> = {}): ContactCardSpec {
    return {
        name: 'Support Triage',
        seed: 'a1b2c3d4',
        icon: null,
        telegram: '@support_triage_bot',
        whatsapp: '+15551234567',
        email: 'triage@pod.lemma.work',
        org: 'Acme Support',
        note: 'Answers customer mail and files the rest.',
        ...overrides,
    };
}

/** Properties, unfolded, the way an importer reads them back. */
function properties(vcard: string): string[] {
    return vcard.replace(/\r\n /g, '').split('\r\n').filter(Boolean);
}

describe('normalizers', () => {
    it('accepts the handle shapes each platform actually issues', () => {
        expect(normalizeTelegram('support_bot')).toBe('@support_bot');
        expect(normalizeTelegram('@support_bot')).toBe('@support_bot');
        expect(normalizeWhatsApp('+1 (555) 123-4567')).toBe('+15551234567');
        expect(normalizeWhatsApp('15551234567')).toBe('+15551234567');
        expect(normalizeEmail('  Triage@Pod.Lemma.Work ')).toBe('triage@pod.lemma.work');
    });

    it('drops what it cannot vouch for rather than repairing it', () => {
        expect(normalizeTelegram('a b')).toBeNull();
        expect(normalizeTelegram('no')).toBeNull();
        expect(normalizeTelegram('bad-handle!')).toBeNull();
        expect(normalizeWhatsApp('call me')).toBeNull();
        expect(normalizeWhatsApp('+0123')).toBeNull();
        expect(normalizeEmail('triage@pod')).toBeNull();
        expect(normalizeEmail('a b@pod.work')).toBeNull();
    });
});

describe('reading a card off a link', () => {
    it('round-trips a full card', () => {
        const params = contactCardParams(spec());
        const read = readContactCardSpec(params, 'Support Triage');
        expect(read).toEqual(spec());
    });

    it('names nothing without a name', () => {
        expect(readContactCardSpec(new URLSearchParams(), null)).toBeNull();
        expect(readContactCardSpec(new URLSearchParams(), '   ')).toBeNull();
    });

    it('falls back to the name when the link carries no seed', () => {
        const read = readContactCardSpec(new URLSearchParams(), 'Support Triage');
        expect(read?.seed).toBe('Support Triage');
    });

    it('keeps a card that only some channels reached', () => {
        const params = contactCardParams(spec({ whatsapp: null, email: null }));
        const read = readContactCardSpec(params, 'Support Triage');
        expect(read?.telegram).toBe('@support_triage_bot');
        expect(read?.whatsapp).toBeNull();
        expect(contactChannels(read!)).toHaveLength(1);
    });

    it('offers Telegram first, then WhatsApp, then mail', () => {
        expect(contactChannels(spec()).map((channel) => channel.key)).toEqual([
            'telegram',
            'whatsapp',
            'email',
        ]);
    });

    it('builds links each platform actually opens', () => {
        const channels = contactChannels(spec());
        expect(channels[0].href).toBe('https://t.me/support_triage_bot');
        expect(channels[1].href).toBe('https://wa.me/15551234567');
        expect(channels[2].href).toBe('mailto:triage@pod.lemma.work');
    });
});

describe('the vCard', () => {
    it('writes the properties a contact app reads', () => {
        const lines = properties(buildVCard(spec(), { revision: REVISION }));
        expect(lines[0]).toBe('BEGIN:VCARD');
        expect(lines[1]).toBe('VERSION:3.0');
        expect(lines).toContain('FN:Support Triage');
        expect(lines).toContain('ORG:Acme Support');
        expect(lines).toContain('TEL;TYPE=CELL:+15551234567');
        expect(lines).toContain('EMAIL;TYPE=INTERNET:triage@pod.lemma.work');
        expect(lines).toContain('URL;TYPE=Telegram:https://t.me/support_triage_bot');
        expect(lines).toContain('REV:20260829T103000Z');
        expect(lines.at(-1)).toBe('END:VCARD');
    });

    it('ends every line, including the last, with CRLF', () => {
        const vcard = buildVCard(spec(), { revision: REVISION });
        expect(vcard.endsWith('END:VCARD\r\n')).toBe(true);
        expect(vcard.includes('\n\n')).toBe(false);
        for (const line of vcard.split('\r\n').slice(0, -1)) {
            expect(line).not.toContain('\n');
        }
    });

    it('keys two pods\' Lems apart, though they share a seed and a face', () => {
        // Lem's seed is one constant across every pod. Keyed on the seed alone,
        // saving a second pod's Lem would overwrite the first one in the address
        // book — the same UID means "this contact", not "another contact".
        const acme = buildVCard(spec({ name: 'Lem · Acme', seed: '__lem__' }), {
            uid: 'pod-acme:__lem__',
            revision: REVISION,
        });
        const beta = buildVCard(spec({ name: 'Lem · Beta', seed: '__lem__' }), {
            uid: 'pod-beta:__lem__',
            revision: REVISION,
        });

        expect(properties(acme)).toContain('UID:urn:lemma:agent:pod-acme:__lem__');
        expect(properties(beta)).toContain('UID:urn:lemma:agent:pod-beta:__lem__');
    });

    it('falls back to the seed when no caller scopes the uid', () => {
        expect(properties(buildVCard(spec(), { revision: REVISION }))).toContain(
            'UID:urn:lemma:agent:a1b2c3d4',
        );
    });

    it('keys the contact on the agent, not on its name', () => {
        const renamed = buildVCard(spec({ name: 'Triage' }), { revision: REVISION });
        expect(properties(renamed)).toContain('UID:urn:lemma:agent:a1b2c3d4');
        expect(properties(buildVCard(spec(), { revision: REVISION }))).toContain(
            'UID:urn:lemma:agent:a1b2c3d4',
        );
    });

    /*
     * The link is typed by anyone and the file is saved by someone who trusts it,
     * so a value that can close its own property and open another is the whole
     * risk this format carries.
     */
    it('cannot be talked into writing properties of its own', () => {
        const params = new URLSearchParams({ o: 'Acme\r\nTEL;TYPE=CELL:+19998887777' });
        const read = readContactCardSpec(params, 'Support\r\nEMAIL:evil@example.com');
        const lines = properties(buildVCard(read!, { revision: REVISION }));

        expect(lines).toContain('FN:Support EMAIL:evil@example.com');
        expect(lines.some((line) => line.startsWith('EMAIL:'))).toBe(false);
        expect(lines.some((line) => line.startsWith('TEL;TYPE=CELL:+19998887777'))).toBe(false);
    });

    it('escapes the separators a value is allowed to contain', () => {
        const read = readContactCardSpec(new URLSearchParams(), 'Sales; Support, and C:\\Ops');
        const lines = properties(buildVCard(read!, { revision: REVISION }));
        expect(lines).toContain('FN:Sales\\; Support\\, and C:\\\\Ops');
    });

    it('folds a long property and unfolds back to what it was', () => {
        const photo = { data: 'A'.repeat(4000), type: 'PNG' as const };
        const vcard = buildVCard(spec(), { photo, revision: REVISION });

        for (const line of vcard.split('\r\n')) {
            expect(new TextEncoder().encode(line).length).toBeLessThanOrEqual(75);
        }
        expect(properties(vcard)).toContain(`PHOTO;ENCODING=b;TYPE=PNG:${photo.data}`);
    });

    it('folds multi-byte names without splitting a character', () => {
        const name = 'ग्राहक सहायता एजेंट'.repeat(6);
        const vcard = buildVCard(spec({ name, seed: 'a1' }), { revision: REVISION });

        for (const line of vcard.split('\r\n')) {
            expect(new TextEncoder().encode(line).length).toBeLessThanOrEqual(75);
        }
        expect(properties(vcard)).toContain(`FN:${name}`);
        expect(vcard).not.toContain('\ufffd');
    });

    it('names the download after the agent', () => {
        expect(contactCardFilename(spec())).toBe('support-triage.vcf');
        expect(contactCardFilename(spec({ name: '???' }))).toBe('agent.vcf');
    });
});
