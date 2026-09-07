import { describe, expect, it } from 'vitest';

import {
    firstSource,
    readChannelContext,
    readCounterpart,
    readSender,
    readSource,
    readSubject,
    senderIsViewer,
    shapeDescription,
    sourceHeadline,
} from './conversation-source';

describe('readSource', () => {
    it('says nothing about a conversation that started here', () => {
        // The common case. A conversation typed in this app has no source to
        // name, and a "Web" badge on every row would say nothing loudly.
        expect(readSource({ metadata: { project: 'x' } })).toBeNull();
        expect(readSource({})).toBeNull();
        expect(readSource(null)).toBeNull();
    });

    it('reads the platform from either metadata spelling', () => {
        // A conversation carries one `metadata`; a message can arrive with
        // either, and the SDK's own reader prefers `message_metadata`.
        expect(readSource({ metadata: { surface_platform: 'WHATSAPP' } })?.label).toBe('WhatsApp');
        expect(readSource({ message_metadata: { surface_platform: 'SLACK' } })?.label).toBe('Slack');
    });

    it('prefers whichever bag actually names a platform', () => {
        // Precedence alone would read the empty bag and conclude the
        // conversation came from nowhere.
        const source = readSource({
            metadata: {},
            message_metadata: { surface_platform: 'TELEGRAM' },
        });
        expect(source?.platform).toBe('TELEGRAM');
    });

    it('ignores a platform this build does not know', () => {
        expect(readSource({ metadata: { surface_platform: 'CARRIER_PIGEON' } })).toBeNull();
    });

    it('calls Resend what the reader calls it', () => {
        // "Resend" is a transport an admin picks; a reader looking at a
        // conversation would have to go and look it up.
        expect(readSource({ metadata: { surface_platform: 'RESEND' } })?.label).toBe('Email');
    });
});

describe('shape', () => {
    it('trusts the backend when it says which shape this is', () => {
        const channel = readSource({
            metadata: { surface_platform: 'SLACK', conversation_kind: 'CHANNEL' },
        });
        expect(channel?.shape).toBe('channel');

        const dm = readSource({
            metadata: { surface_platform: 'SLACK', conversation_kind: 'DM' },
        });
        expect(dm?.shape).toBe('dm');
    });

    it('falls back to a chat, never to a channel', () => {
        // Rows written before the kind was stored. Mail is settled by the
        // platform; claiming "channel" unasked would put two names on a
        // conversation that had eight people in it.
        expect(readSource({ metadata: { surface_platform: 'SLACK' } })?.shape).toBe('dm');
        expect(readSource({ metadata: { surface_platform: 'RESEND' } })?.shape).toBe('mail');
    });
});

describe('channel', () => {
    it('prefixes a name it was given', () => {
        const source = readSource({
            metadata: {
                surface_platform: 'SLACK',
                conversation_kind: 'CHANNEL',
                channel_name: 'field-ops',
            },
        });
        expect(source?.channel).toBe('#field-ops');
        expect(sourceHeadline(source!)).toBe('Slack · #field-ops');
    });

    it('does not double the prefix', () => {
        const source = readSource({
            metadata: {
                surface_platform: 'SLACK',
                conversation_kind: 'CHANNEL',
                channel_name: '#field-ops',
            },
        });
        expect(source?.channel).toBe('#field-ops');
    });

    it('names no place when nobody named the channel', () => {
        // Only the id reaches us for an unrouted channel, and `C07AB12CD` reads
        // as a bug rather than as a place.
        const source = readSource({
            metadata: {
                surface_platform: 'SLACK',
                conversation_kind: 'CHANNEL',
                external_channel_id: 'C07AB12CD',
            },
        });
        expect(source?.channel).toBeNull();
        expect(sourceHeadline(source!)).toBe('Slack');
        expect(shapeDescription(source!)).toBe('Channel message');
    });

    it('never names a channel on a conversation that is not one', () => {
        const source = readSource({
            metadata: {
                surface_platform: 'SLACK',
                conversation_kind: 'DM',
                channel_name: 'field-ops',
            },
        });
        expect(source?.channel).toBeNull();
    });
});

describe('readSender', () => {
    it('is silent for a message that started here', () => {
        // No surface, no other human: the bubble is the reader's own voice and
        // attributing it to somebody would be the bug, not the fix.
        expect(readSender({ metadata: { sender_display_name: 'Ravi' } })).toBeNull();
    });

    it('prints the best line it has', () => {
        const named = readSender({
            metadata: { surface_platform: 'SLACK', sender_display_name: 'Ravi Kumar' },
        });
        expect(named?.label).toBe('Ravi Kumar');

        const emailed = readSender({
            metadata: { surface_platform: 'RESEND', sender_email: 'ravi@example.com' },
        });
        expect(emailed?.label).toBe('ravi@example.com');

        const phoned = readSender({
            metadata: { surface_platform: 'WHATSAPP', sender_phone: '+91 98765 43210' },
        });
        expect(phoned?.label).toBe('+91 98765 43210');
    });

    it('is null when the surface never learned who they were', () => {
        expect(readSender({ metadata: { surface_platform: 'SLACK' } })).toBeNull();
    });
});

describe('readChannelContext', () => {
    it('reads the background messages a group run was given', () => {
        const entries = readChannelContext({
            metadata: {
                surface_platform: 'SLACK',
                channel_context: [
                    { author: 'Priya', text: 'we ship on Friday', ts: '1712.0' },
                    { author: null, text: 'ack' },
                ],
            },
        });
        expect(entries).toEqual([
            { author: 'Priya', text: 'we ship on Friday', ts: '1712.0' },
            { author: null, text: 'ack', ts: null },
        ]);
    });

    it('drops entries with nothing to show', () => {
        const entries = readChannelContext({
            metadata: { surface_platform: 'SLACK', channel_context: [{ author: 'Priya' }, 'nope'] },
        });
        expect(entries).toEqual([]);
    });

    it('survives a shape it did not expect', () => {
        // This runs during a render, so anything in the bag has to come back as
        // a list rather than a throw.
        expect(readChannelContext({ metadata: { channel_context: 'sure' } })).toEqual([]);
        expect(readChannelContext(null)).toEqual([]);
    });
});

describe('readSubject', () => {
    it('reads a subject only where one exists', () => {
        expect(readSubject({
            metadata: { surface_platform: 'RESEND', subject: 'Invoice 4021' },
        })).toBe('Invoice 4021');
        // Slack has no subjects; a `subject` key there is somebody else's field.
        expect(readSubject({
            metadata: { surface_platform: 'SLACK', subject: 'Invoice 4021' },
        })).toBeNull();
    });
});

describe('firstSource', () => {
    it('takes the first message that names a source', () => {
        // A partially loaded history can open on a reply that carries no
        // metadata, so this scans rather than reading message zero.
        const source = firstSource([
            { metadata: {} },
            null,
            { metadata: { surface_platform: 'TELEGRAM' } },
        ]);
        expect(source?.platform).toBe('TELEGRAM');
    });

    it('is null for a conversation that started here', () => {
        expect(firstSource([{ metadata: {} }, { metadata: {} }])).toBeNull();
        expect(firstSource([])).toBeNull();
    });
});

describe('senderIsViewer', () => {
    const sender = readSender({
        metadata: {
            surface_platform: 'WHATSAPP',
            sender_display_name: 'Deepak',
            sender_email: 'Deepak@Example.com',
        },
    })!;

    it('matches on email regardless of case or padding', () => {
        expect(senderIsViewer(sender, { email: ' deepak@example.com ' })).toBe(true);
    });

    it('matches on name when there is no email to compare', () => {
        expect(senderIsViewer(sender, { name: 'deepak' })).toBe(true);
    });

    it('does not match a different person', () => {
        expect(senderIsViewer(sender, { email: 'priya@example.com', name: 'Priya' })).toBe(false);
    });

    it('claims nothing when there is nobody to compare against', () => {
        // Signed-out, or the auth state still loading. Guessing "this is you"
        // from an absent viewer would hide a real sender.
        expect(senderIsViewer(sender, null)).toBe(false);
        expect(senderIsViewer(sender, { email: null, name: null })).toBe(false);
    });
});

describe('readCounterpart', () => {
    const fromRavi = {
        metadata: {
            surface_platform: 'SLACK',
            conversation_kind: 'CHANNEL',
            sender_display_name: 'Ravi Kumar',
            sender_email: 'ravi@acme.com',
        },
    };
    const fromMe = {
        metadata: {
            surface_platform: 'WHATSAPP',
            conversation_kind: 'DM',
            sender_display_name: 'Deepak',
            sender_email: 'deepak@example.com',
        },
    };

    it('names the other human', () => {
        expect(readCounterpart([fromRavi], { email: 'deepak@example.com' })?.label)
            .toBe('Ravi Kumar');
    });

    it('says nothing when the sender is the reader', () => {
        // The usual case, and the reason this exists: a conversation holds one
        // member's messages, and the copy you are reading is your own. Printing
        // your own name over your own message is not attribution.
        expect(readCounterpart([fromMe], { email: 'deepak@example.com' })).toBeNull();
    });

    it('does not look past the sender it found', () => {
        // A conversation has exactly one human. If the first one is the reader,
        // the answer is "nobody else" — not "keep looking until something
        // matches", which would name a channel-context author as the sender.
        expect(readCounterpart([fromMe, fromRavi], { name: 'Deepak' })).toBeNull();
    });

    it('is null for a conversation that started here', () => {
        expect(readCounterpart([{ metadata: {} }], { email: 'deepak@example.com' })).toBeNull();
    });
});
