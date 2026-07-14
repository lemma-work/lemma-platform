import { describe, expect, it } from 'vitest';
import type { AssistantToolInvocation } from 'lemma-sdk/react';

import {
    deriveSubagentActivities,
    isSubagentLifecycleToolName,
    mergeSubagentConversationSnapshots,
    subagentActivityPhase,
    summarizeSubagentActivities,
} from '../subagent-activity';

function invocation(
    toolCallId: string,
    toolName: string,
    args: Record<string, unknown>,
    result?: Record<string, unknown>,
): AssistantToolInvocation {
    return {
        toolCallId,
        toolName,
        args,
        state: result ? 'result' : 'call',
        ...(result ? { result } : {}),
    };
}

describe('sub-agent activity aggregation', () => {
    it('folds spawn and await lifecycle calls into one completed child', () => {
        const activities = deriveSubagentActivities([
            invocation('spawn-1', 'spawn_subagent', {
                input: 'Research the tournament format',
            }, {
                success: true,
                conversation_id: 'child-1',
                run_id: 'run-1',
                status: 'RUNNING',
            }),
            invocation('await-1', 'interact_subagent', {
                action: 'await',
                conversation_id: 'child-1',
                run_id: 'run-1',
            }, {
                success: true,
                conversation_id: 'child-1',
                run_id: 'run-1',
                status: 'COMPLETED',
                output: { answer: 'The tournament has 48 teams.' },
            }),
        ]);

        expect(activities).toHaveLength(1);
        expect(activities[0]).toMatchObject({
            conversationId: 'child-1',
            runId: 'run-1',
            task: 'Research the tournament format',
            status: 'COMPLETED',
            output: 'The tournament has 48 teams.',
        });
        expect(subagentActivityPhase(activities[0].status)).toBe('complete');
    });

    it('uses child snapshots as the live source of status and final output', () => {
        const derived = deriveSubagentActivities([
            invocation('spawn-1', 'spawn_subagent', { input: 'Find host cities' }, {
                success: true,
                conversation_id: 'child-1',
                run_id: 'run-1',
                status: 'RUNNING',
            }),
            invocation('spawn-2', 'spawn_subagent', { input: 'Find current news' }, {
                success: true,
                conversation_id: 'child-2',
                run_id: 'run-2',
                status: 'RUNNING',
            }),
        ]);

        const activities = mergeSubagentConversationSnapshots(derived, [
            {
                id: 'child-1',
                status: 'COMPLETED',
                output: { answer: 'Sixteen host cities.' },
            },
            {
                id: 'child-2',
                status: 'FAILED',
                last_run_error: 'Search provider rejected the request.',
            },
        ]);

        expect(activities[0]).toMatchObject({
            status: 'COMPLETED',
            output: 'Sixteen host cities.',
        });
        expect(activities[1]).toMatchObject({
            status: 'FAILED',
            error: 'Search provider rejected the request.',
        });
        expect(summarizeSubagentActivities(activities)).toBe(
            '2 sub-agents · 1 complete · 1 failed',
        );
    });

    it('recognizes normalized sub-agent tools and ignores ordinary parent tools', () => {
        expect(isSubagentLifecycleToolName('mcp__lemma_tools__lemma_spawn_subagent')).toBe(true);
        expect(isSubagentLifecycleToolName('interact_subagent')).toBe(true);
        expect(isSubagentLifecycleToolName('query_subagents')).toBe(true);
        expect(isSubagentLifecycleToolName('web_search')).toBe(false);
    });
});
