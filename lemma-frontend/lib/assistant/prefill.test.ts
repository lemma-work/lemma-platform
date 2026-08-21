import { describe, expect, it } from 'vitest';

import { parseAssistantPrefillDetail } from './prefill';

describe('parseAssistantPrefillDetail', () => {
    it('keeps a real message and trims it', () => {
        expect(parseAssistantPrefillDetail({ content: '  make this denser  ' })).toEqual({
            content: 'make this denser',
            forceNewConversation: false,
        });
    });

    it('opts into a new conversation only when asked explicitly', () => {
        expect(
            parseAssistantPrefillDetail({ content: 'hi', forceNewConversation: true }),
        ).toEqual({ content: 'hi', forceNewConversation: true });
        expect(
            parseAssistantPrefillDetail({ content: 'hi', forceNewConversation: 'yes' }),
        ).toEqual({ content: 'hi', forceNewConversation: false });
    });

    it('rejects anything that would open the assistant with nothing to say', () => {
        expect(parseAssistantPrefillDetail(null)).toBeNull();
        expect(parseAssistantPrefillDetail('make this denser')).toBeNull();
        expect(parseAssistantPrefillDetail({})).toBeNull();
        expect(parseAssistantPrefillDetail({ content: '   ' })).toBeNull();
        expect(parseAssistantPrefillDetail({ content: 42 })).toBeNull();
    });
});
