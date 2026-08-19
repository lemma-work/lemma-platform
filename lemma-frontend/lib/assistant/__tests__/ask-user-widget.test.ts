import { describe, it, expect } from 'vitest';

import {
    coerceWidgetAnswers,
    parseAskUserQuestions,
    parseAskUserWidget,
} from '@/components/lemma/assistant/assistant-approval-cards';

const QUESTIONS = parseAskUserQuestions({
    questions: [
        {
            question: 'Where should approvals go?',
            header: 'Approvals',
            options: [{ label: 'To me' }, { label: 'Whoever is on duty' }],
        },
    ],
} as never);

const MULTI = parseAskUserQuestions({
    questions: [
        {
            question: 'Which channels?',
            header: 'Channels',
            multi_select: true,
            options: [{ label: 'Telegram' }, { label: 'Slack' }, { label: 'Email' }],
        },
    ],
} as never);

describe('parseAskUserWidget', () => {
    it('reads inline content and caps loading messages', () => {
        const widget = parseAskUserWidget({
            content: '<div>pick</div>',
            loading_messages: ['a', 'b', 'c', 'd', 'e'],
        } as never);

        expect(widget.content).toBe('<div>pick</div>');
        expect(widget.loadingMessages).toHaveLength(4);
    });

    it('treats blank content as absent, so the chips are shown instead', () => {
        expect(parseAskUserWidget({ content: '   ' } as never).content).toBeNull();
        expect(parseAskUserWidget({} as never).content).toBeNull();
    });
});

describe('coerceWidgetAnswers', () => {
    it('accepts a declared option label', () => {
        expect(coerceWidgetAnswers(QUESTIONS, { Approvals: 'To me' })).toEqual({
            Approvals: 'To me',
        });
    });

    it('rejects an answer for a question that was never asked', () => {
        expect(coerceWidgetAnswers(QUESTIONS, { SomethingElse: 'To me' })).toBeNull();
    });

    it('ignores extra keys the widget invented', () => {
        expect(
            coerceWidgetAnswers(QUESTIONS, { Approvals: 'To me', Injected: 'ignore me' }),
        ).toEqual({ Approvals: 'To me' });
    });

    it('rejects non-string answers rather than coercing them', () => {
        expect(coerceWidgetAnswers(QUESTIONS, { Approvals: { evil: true } })).toBeNull();
        expect(coerceWidgetAnswers(QUESTIONS, { Approvals: 42 })).toBeNull();
        expect(coerceWidgetAnswers(QUESTIONS, { Approvals: '  ' })).toBeNull();
    });

    it('keeps free text, because the chips allow it through "Other"', () => {
        expect(coerceWidgetAnswers(QUESTIONS, { Approvals: 'my ops rota' })).toEqual({
            Approvals: 'my ops rota',
        });
    });

    it('caps free text, which nothing else bounds in a widget', () => {
        const answers = coerceWidgetAnswers(QUESTIONS, { Approvals: 'x'.repeat(10_000) });
        expect((answers?.Approvals as string).length).toBe(4000);
    });

    it('requires every question to be answered, matching the chips', () => {
        const two = parseAskUserQuestions({
            questions: [
                { question: 'A?', header: 'A', options: [{ label: 'yes' }, { label: 'no' }] },
                { question: 'B?', header: 'B', options: [{ label: 'yes' }, { label: 'no' }] },
            ],
        } as never);

        expect(coerceWidgetAnswers(two, { A: 'yes' })).toBeNull();
        expect(coerceWidgetAnswers(two, { A: 'yes', B: 'no' })).toEqual({ A: 'yes', B: 'no' });
    });

    it('takes a list only for a multi-select question', () => {
        expect(coerceWidgetAnswers(MULTI, { Channels: ['Telegram', 'Slack'] })).toEqual({
            Channels: ['Telegram', 'Slack'],
        });
        expect(coerceWidgetAnswers(MULTI, { Channels: 'Telegram' })).toBeNull();
        expect(coerceWidgetAnswers(QUESTIONS, { Approvals: ['To me'] })).toBeNull();
    });

    it('drops empty entries and refuses an all-empty list', () => {
        expect(coerceWidgetAnswers(MULTI, { Channels: ['Telegram', '', '  '] })).toEqual({
            Channels: ['Telegram'],
        });
        expect(coerceWidgetAnswers(MULTI, { Channels: [] })).toBeNull();
    });

    it('returns null when there are no questions to answer', () => {
        expect(coerceWidgetAnswers([], { Anything: 'x' })).toBeNull();
    });
});
