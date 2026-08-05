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

    it('keeps the controller copy for the conversation it is driving', () => {
        const result = mergeSidebarConversations(
            [conversation('shared', '2026-07-17T10:00:00.000Z', { status: 'completed' })],
            [conversation('shared', '2026-07-17T10:00:00.000Z', { status: 'running' })],
            'shared',
        );

        expect(result).toHaveLength(1);
        expect(result[0]?.status).toBe('running');
    });

    it('drops a controller status for a conversation it is no longer driving', () => {
        // The controller watched this run start and never saw it end, so its
        // copy says running forever. History is the only party with a clock.
        const result = mergeSidebarConversations(
            [conversation('left', '2026-07-17T10:00:00.000Z', { status: 'completed' })],
            [conversation('left', '2026-07-17T10:00:00.000Z', { status: 'running' })],
            'somewhere-else',
        );

        expect(result).toHaveLength(1);
        expect(result[0]?.status).toBe('completed');
    });

    it('keeps a conversation history has not seen yet, whoever is being driven', () => {
        const result = mergeSidebarConversations(
            [],
            [conversation('just-created', '2026-07-17T10:00:00.000Z', { status: 'running' })],
            null,
        );

        expect(result.map((item) => item.id)).toEqual(['just-created']);
        expect(result[0]?.status).toBe('running');
    });

    it('takes the later timestamp from a controller copy it otherwise discards', () => {
        // Otherwise a row the controller moved would drop back down the list
        // until the next refetch, then climb again.
        const result = mergeSidebarConversations(
            [
                conversation('moved', '2026-07-17T10:00:00.000Z', { status: 'completed' }),
                conversation('other', '2026-07-18T10:00:00.000Z'),
            ],
            [conversation('moved', '2026-07-19T10:00:00.000Z', { status: 'running' })],
            null,
        );

        expect(result.map((item) => item.id)).toEqual(['moved', 'other']);
        expect(result[0]?.status).toBe('completed');
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
