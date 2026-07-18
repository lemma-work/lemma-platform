import { describe, expect, it } from 'vitest';

import { mergeSidebarConversations } from '@/lib/assistant/sidebar-conversations';
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
