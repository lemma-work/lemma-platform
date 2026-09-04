import { describe, it, expect } from 'vitest';

import { parseAskUserQuestions } from '@/components/lemma/assistant/assistant-approval-cards';

describe('parseAskUserQuestions', () => {
    it('parses questions with options, flags, and icons', () => {
        const questions = parseAskUserQuestions({
            questions: [
                {
                    question: 'Which widget should I build?',
                    header: 'Widget',
                    multi_select: false,
                    options: [
                        { label: 'Donut chart', description: 'Share of total', recommended: true, icon: '🍩' },
                        { label: 'Heatmap', icon: '🗓️' },
                    ],
                },
            ],
        } as never);

        expect(questions).toHaveLength(1);
        expect(questions[0]).toMatchObject({ header: 'Widget', multiSelect: false });
        expect(questions[0].options).toEqual([
            { label: 'Donut chart', description: 'Share of total', recommended: true, icon: '🍩' },
            { label: 'Heatmap', description: undefined, recommended: false, icon: '🗓️' },
        ]);
    });

    it('drops questions without a header or without options, and options without a label', () => {
        const questions = parseAskUserQuestions({
            questions: [
                { question: 'No header', options: [{ label: 'A' }, { label: 'B' }] },
                { question: 'No options', header: 'Empty', options: [] },
                {
                    question: 'Kept',
                    header: 'Kept',
                    options: [{ label: '' }, { label: 'Only one' }, { label: 'Two' }],
                },
            ],
        } as never);

        expect(questions).toHaveLength(1);
        expect(questions[0].options.map((option) => option.label)).toEqual(['Only one', 'Two']);
    });

    it('reads multi_select and defaults icon/description/recommended when absent', () => {
        const questions = parseAskUserQuestions({
            questions: [
                {
                    question: 'Pick sources',
                    header: 'Sources',
                    multi_select: true,
                    options: [{ label: 'Inbox' }, { label: 'RSS' }],
                },
            ],
        } as never);

        expect(questions[0].multiSelect).toBe(true);
        expect(questions[0].options[0]).toEqual({
            label: 'Inbox',
            description: undefined,
            recommended: false,
            icon: undefined,
        });
    });
});
