import { describe, expect, it } from 'vitest';

import {
    filterSidebarConversations,
    mergeSidebarConversations,
    SIDEBAR_CONVERSATION_LIMIT,
} from '@/lib/assistant/sidebar-conversations';
import type { Conversation } from '@/lib/types';

function conversation(
    id: string,
    updatedAt: string,
    overrides: Partial<Conversation> = {},
): Conversation {
    return {
        id,
        title: id,
        created_at: updatedAt,
        updated_at: updatedAt,
        ...overrides,
    } as Conversation;
}

describe('mergeSidebarConversations', () => {
    it('renders cold pod history by recency without requiring controller state', () => {
        const result = mergeSidebarConversations([
            conversation('older', '2026-07-15T10:00:00.000Z'),
            conversation('newer', '2026-07-17T10:00:00.000Z'),
        ], []);

        expect(result.map((item) => item.id)).toEqual(['newer', 'older']);
    });

    it('keeps the controller copy when it has fresher local status', () => {
        const result = mergeSidebarConversations(
            [conversation('shared', '2026-07-17T10:00:00.000Z', { status: 'completed' })],
            [conversation('shared', '2026-07-17T10:00:00.000Z', { status: 'running' })],
        );

        expect(result).toHaveLength(1);
        expect(result[0]?.status).toBe('running');
    });
});

describe('mergeSidebarConversations capping', () => {
    it('keeps the newest rows when the merged list is trimmed to the sidebar limit', () => {
        // A controller-only conversation is newer than everything the capped
        // query returned, so it must survive the trim and evict the oldest row.
        const history = Array.from({ length: SIDEBAR_CONVERSATION_LIMIT }, (_, index) =>
            conversation(`history-${index}`, `2026-07-${String(10 + index).padStart(2, '0')}T10:00:00.000Z`));
        const merged = mergeSidebarConversations(
            history,
            [conversation('live', '2026-07-31T10:00:00.000Z')],
        ).slice(0, SIDEBAR_CONVERSATION_LIMIT);

        expect(merged).toHaveLength(SIDEBAR_CONVERSATION_LIMIT);
        expect(merged[0]?.id).toBe('live');
        expect(merged.map((item) => item.id)).not.toContain('history-0');
    });
});

describe('filterSidebarConversations', () => {
    const conversations = [
        conversation('a', '2026-07-31T10:00:00.000Z', { title: 'Renewal triage' }),
        conversation('b', '2026-07-31T10:00:00.000Z', { title: 'Ticket sweep' }),
        conversation('c', '2026-07-31T10:00:00.000Z', { title: null }),
    ];

    it('passes everything through for an empty query', () => {
        expect(filterSidebarConversations(conversations, '   ')).toHaveLength(3);
    });

    it('matches titles case-insensitively on a substring', () => {
        const result = filterSidebarConversations(conversations, 'TRIA');
        expect(result.map((item) => item.id)).toEqual(['a']);
    });

    it('matches untitled conversations by their fallback label', () => {
        const result = filterSidebarConversations(conversations, 'untitled');
        expect(result.map((item) => item.id)).toEqual(['c']);
    });
});
