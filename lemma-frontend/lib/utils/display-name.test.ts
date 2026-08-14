import { describe, expect, it } from 'vitest';
import { humanizeName } from './display-name';

describe('humanizeName', () => {
    it('turns separators into spaces', () => {
        expect(humanizeName('customer-support_bot')).toBe('Customer support bot');
        expect(humanizeName('ledflex--support')).toBe('Ledflex support');
        expect(humanizeName('  spaced   out  ')).toBe('Spaced out');
    });

    it('capitalises the first letter only', () => {
        // `docs/design-tokens.md`: product copy is sentence case. This used to
        // title-case every word, so a pod of resources read as Proper Nouns.
        expect(humanizeName('invoice-triage')).toBe('Invoice triage');
        expect(humanizeName('nightly-sync-of-orders')).toBe('Nightly sync of orders');
    });

    it('leaves version fragments alone', () => {
        expect(humanizeName('deepseek-v4-flash')).toBe('Deepseek v4 flash');
        expect(humanizeName('minimax-m3')).toBe('Minimax m3');
    });

    it('shouts the words that are acronyms', () => {
        // Sentence case alone renders `gpt-4o` as "Gpt 4o", which is not
        // humanising the name so much as misspelling it.
        expect(humanizeName('gpt-4o')).toBe('GPT 4o');
        expect(humanizeName('csv-import')).toBe('CSV import');
        expect(humanizeName('sync-to-api')).toBe('Sync to API');
    });

    it('does not flatten a capital that was already there', () => {
        expect(humanizeName('deploy-Slack-bot')).toBe('Deploy Slack bot');
    });

    it('survives empty input', () => {
        expect(humanizeName('')).toBe('');
        expect(humanizeName('---')).toBe('');
    });
});
