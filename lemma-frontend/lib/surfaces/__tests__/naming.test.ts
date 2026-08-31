import { describe, expect, it } from 'vitest';

import { deriveSurfaceName } from '@/lib/surfaces/naming';

const named = (...names: string[]) => names.map((name) => ({ name }));

describe('naming a new surface', () => {
    it('lets the first of a platform take the backend default', () => {
        expect(deriveSurfaceName('TELEGRAM', 'Ops Assistant', [])).toBeUndefined();
        expect(deriveSurfaceName('TELEGRAM', 'Ops', named('slack', 'gmail'))).toBeUndefined();
    });

    // The bug this exists for: a second agent connecting Telegram must get its
    // own surface, not collide with (or reopen) the first agent's.
    it('names the second surface of a platform for its agent', () => {
        expect(deriveSurfaceName('TELEGRAM', 'Ops Assistant', named('telegram'))).toBe(
            'telegram-ops-assistant',
        );
    });

    it('keeps going when the same agent takes a second bot', () => {
        expect(
            deriveSurfaceName('TELEGRAM', 'Ops', named('telegram', 'telegram-ops')),
        ).toBe('telegram-ops-2');
        expect(
            deriveSurfaceName('TELEGRAM', 'Ops', named('telegram', 'telegram-ops', 'telegram-ops-2')),
        ).toBe('telegram-ops-3');
    });

    it('separates agents whose names slugify alike', () => {
        // "Ops Bot" and "ops-bot" both slugify to ops-bot; the second still gets
        // a distinct surface rather than failing to create.
        expect(
            deriveSurfaceName('TELEGRAM', 'Ops Bot', named('telegram', 'telegram-ops-bot')),
        ).toBe('telegram-ops-bot-2');
    });

    it('names the pod default assistant’s surface without an agent', () => {
        expect(deriveSurfaceName('TELEGRAM', null, named('telegram'))).toBe('telegram-default');
    });

    it('strips punctuation and casing out of agent names', () => {
        expect(deriveSurfaceName('SLACK', '  Sales & Support!  ', named('slack'))).toBe(
            'slack-sales-support',
        );
    });

    it('never emits a trailing dash from a truncated agent name', () => {
        const long = 'a'.repeat(30) + ' ' + 'b'.repeat(30);
        const derived = deriveSurfaceName('SLACK', long, named('slack'))!;
        expect(derived.endsWith('-')).toBe(false);
        expect(derived.startsWith('slack-')).toBe(true);
    });

    it('matches existing names case-insensitively', () => {
        // Names are pod-unique without regard to case, so a differently-cased
        // match still has to count as taken.
        expect(deriveSurfaceName('TELEGRAM', 'Ops', named('Telegram'))).toBe('telegram-ops');
    });

    describe('platforms the backend names itself', () => {
        // Resend is Lemma's own mailbox: one per agent, minted with the agent,
        // resolved by agent binding. Sending a name asks for a *second*
        // surface, which is how connecting email produced a duplicate mailbox
        // on a suffixed address instead of connecting the existing one.
        it('never names a Resend surface, whatever the pod already holds', () => {
            expect(deriveSurfaceName('RESEND', 'Ops Assistant', [])).toBeUndefined();
            expect(deriveSurfaceName('RESEND', 'Ops Assistant', named('resend'))).toBeUndefined();
            expect(deriveSurfaceName('RESEND', null, named('resend-assistant'))).toBeUndefined();
        });

        it('does not care what case the platform arrives in', () => {
            expect(deriveSurfaceName('resend', 'Ops', named('resend'))).toBeUndefined();
        });

        it('still names the platforms a pod can hold several of', () => {
            // The rule above is not "stop naming surfaces". A pod really can run
            // two Telegram bots, and the second still needs a name of its own.
            expect(deriveSurfaceName('TELEGRAM', 'Ops', named('telegram'))).toBe('telegram-ops');
            expect(deriveSurfaceName('SLACK', 'Ops', named('slack'))).toBe('slack-ops');
        });
    });
});
