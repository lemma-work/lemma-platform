import { describe, expect, it } from 'vitest';

import { NEW_POD_OPENING_MESSAGE } from './new-pod-conversation';
import {
    POD_WELCOME_OPTIONS,
    POD_WELCOME_OWN_WORDS_INSTRUCTIONS,
    POD_WELCOME_SURPRISE,
    podWelcomeOption,
} from './pod-welcome';

describe('the four things a new pod offers', () => {
    it('sends a real sentence, which is the whole reason the door exists', () => {
        for (const option of POD_WELCOME_OPTIONS) {
            expect(option.message).not.toBe(NEW_POD_OPENING_MESSAGE);
            expect(option.message.length).toBeGreaterThan(12);
            expect(option.message.trim()).toBe(option.message);
        }
    });

    it('puts chat first, because it is the fastest thing here that can be true', () => {
        expect(POD_WELCOME_OPTIONS[0].id).toBe('surface');
        expect(POD_WELCOME_OPTIONS.map((option) => option.id)).toEqual([
            'surface',
            'app',
            'agent',
            'people',
        ]);
    });

    it('sets Telegram up rather than offering it, since clicking was the answer', () => {
        const surface = podWelcomeOption('surface');
        expect(surface?.instructions).toContain('lemma surfaces telegram-setup');
        expect(surface?.instructions).toContain('do not ask whether they are sure');
    });

    it('asks one question first wherever the click left the brief missing', () => {
        for (const id of ['app', 'agent'] as const) {
            const instructions = podWelcomeOption(id)?.instructions ?? '';
            expect(instructions).toContain('One question, then stop.');
        }
    });

    it('keeps the note to one line, because a second one is the paragraph we removed', () => {
        for (const option of POD_WELCOME_OPTIONS) {
            expect(option.note.length).toBeLessThanOrEqual(40);
            expect(option.note.split('. ').length).toBe(1);
        }
    });

    it('treats what somebody typed as the brief, not as a hint', () => {
        expect(POD_WELCOME_OWN_WORDS_INSTRUCTIONS).toContain('it is the brief');
    });

    it('keeps one path that asks for nothing at all', () => {
        // The field is the most work on the door and lands on whoever is least
        // able to do it. This is the way through for them.
        expect(POD_WELCOME_SURPRISE.message).toContain('Surprise me');
        expect(POD_WELCOME_SURPRISE.instructions).toContain('Do not ask a question');
        expect(POD_WELCOME_SURPRISE.instructions).toContain('display_resource');
    });

    it('spends a surprise on a widget, never on resources nobody asked for', () => {
        expect(POD_WELCOME_SURPRISE.instructions).toContain(
            'Do not create tables, agents, apps or workflows',
        );
    });

    it('leaves the surprise out of the cards, which are capped at four', () => {
        expect(POD_WELCOME_OPTIONS).toHaveLength(4);
        expect(POD_WELCOME_OPTIONS.map((option) => option.id)).not.toContain('surprise');
        expect(podWelcomeOption('surprise')).toBe(POD_WELCOME_SURPRISE);
    });

    it('has no option for an id nobody offered', () => {
        expect(podWelcomeOption('telegram')).toBeNull();
        expect(podWelcomeOption(null)).toBeNull();
    });
});
