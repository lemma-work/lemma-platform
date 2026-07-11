import { describe, it, expect } from 'vitest';

import {
    buildDisplayResourceHref,
    extractDisplayResourceRequest,
    isDisplayResourceToolName,
} from '../display-resource';

describe('isDisplayResourceToolName', () => {
    it('matches display_resource name variants', () => {
        expect(isDisplayResourceToolName('display_resource')).toBe(true);
        expect(isDisplayResourceToolName('lemma_display_resource')).toBe(true);
        expect(isDisplayResourceToolName('mcp.display_resource')).toBe(true);
        expect(isDisplayResourceToolName('something_else')).toBe(false);
        expect(isDisplayResourceToolName(123)).toBe(false);
    });
});

describe('extractDisplayResourceRequest', () => {
    it('normalizes snake_case public_url + loading_messages', () => {
        const req = extractDisplayResourceRequest({
            type: 'widget',
            public_url: 'https://x.test',
            loading_messages: ['a', 'b'],
        });
        expect(req?.type).toBe('WIDGET');
        expect(req?.publicUrl).toBe('https://x.test');
        expect(req?.loadingMessages).toEqual(['a', 'b']);
    });

    it('returns null for an unknown type', () => {
        expect(extractDisplayResourceRequest({ type: 'nope' })).toBeNull();
    });
});

describe('buildDisplayResourceHref — WIDGET variants', () => {
    const base = { podId: 'p1', conversationId: 'c1', toolCallId: 't1' };

    it('content widget carries only the tool context (no src/path)', () => {
        const href = buildDisplayResourceHref({
            ...base,
            request: { type: 'WIDGET', loadingMessages: ['Reading records', 'Drawing view'] },
        });
        expect(href).toContain('/pod/p1/widgets/view');
        expect(href).toContain('toolCallId=t1');
        expect(href).toContain('assistantConversationId=c1');
        expect(href).not.toContain('src=');
        expect(href).not.toContain('path=');
        const params = new URL(href!, 'https://lemma.work').searchParams;
        expect(params.getAll('loadingMessage')).toEqual(['Reading records', 'Drawing view']);
    });

    it('public_url widget carries an external src', () => {
        const href = buildDisplayResourceHref({
            ...base,
            request: { type: 'WIDGET', publicUrl: 'https://ext.test/app', loadingMessages: [] },
        });
        expect(href).toContain('src=');
        expect(decodeURIComponent(href!)).toContain('https://ext.test/app');
    });

    it('path widget is not routable', () => {
        const href = buildDisplayResourceHref({
            ...base,
            request: { type: 'WIDGET', path: '/KB/w.html', loadingMessages: [] },
        });
        expect(href).toBeNull();
    });
});
