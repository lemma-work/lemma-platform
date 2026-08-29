import { describe, expect, it } from 'vitest';
import type { AssistantToolInvocation } from 'lemma-sdk/react';

import {
    deriveSubagentActivities,
    isSubagentLifecycleToolName,
    mergeSubagentConversationSnapshots,
    readableSubagentTask,
    subagentActivitiesFor,
    subagentActivityPhase,
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
        expect(activities.map((activity) => subagentActivityPhase(activity.status, activity.error)))
            .toEqual(['complete', 'failed']);
    });

    it('recognizes normalized sub-agent tools and ignores ordinary parent tools', () => {
        expect(isSubagentLifecycleToolName('mcp__lemma_tools__lemma_spawn_subagent')).toBe(true);
        expect(isSubagentLifecycleToolName('interact_subagent')).toBe(true);
        expect(isSubagentLifecycleToolName('query_subagents')).toBe(true);
        expect(isSubagentLifecycleToolName('web_search')).toBe(false);
    });
});

describe('which sub-agents a turn owns', () => {
    const spawn = (toolCallId: string, conversationId: string, task: string) => invocation(
        toolCallId,
        'spawn_subagent',
        { input: task },
        { success: true, conversation_id: conversationId, status: 'RUNNING' },
    );

    it('gives a turn only the children its own calls spawned', () => {
        const seeds = deriveSubagentActivities([spawn('spawn-1', 'child-1', 'Draft the brief')]);
        // A sibling spawned by another turn, and a child of this parent whose
        // spawn call is outside the loaded window: the merge folds both in,
        // and neither belongs to this turn.
        const activities = mergeSubagentConversationSnapshots(seeds, [
            { id: 'child-1', last_run_status: 'COMPLETED' },
            { id: 'child-2', last_run_status: 'COMPLETED' },
        ]);

        expect(activities).toHaveLength(2);
        expect(subagentActivitiesFor(activities, seeds).map((activity) => activity.conversationId))
            .toEqual(['child-1']);
    });

    it('keeps a working child and a finished one in the same row', () => {
        const seeds = deriveSubagentActivities([
            spawn('spawn-1', 'child-1', 'Research what is hot in AI'),
            spawn('spawn-2', 'child-2', 'Turn the research into a PDF'),
        ]);
        const activities = mergeSubagentConversationSnapshots(seeds, [
            { id: 'child-1', last_run_status: 'COMPLETED' },
            { id: 'child-2', last_run_status: 'RUNNING' },
        ]);

        expect(subagentActivitiesFor(activities, seeds).map(
            (activity) => subagentActivityPhase(activity.status, activity.error),
        )).toEqual(['complete', 'working']);
    });

    it('reads a child that failed as failed even when its status still says running', () => {
        expect(subagentActivityPhase('RUNNING', 'The sandbox went away.')).toBe('failed');
    });
});

describe('reading a sub-agent brief back to a person', () => {
    /* The real prompt that put "Task id: 0da4bc62-ec07-4d41-…" on a chip: the
       agent leads with the row it is working from, so the first forty
       characters — the only ones a chip has room for — are an identifier. */
    it('drops an opening clause that exists to carry a row id', () => {
        expect(readableSubagentTask(
            "Task id: 0da4bc62-ec07-4d41-82e2-7afcc15b9efa (tasks table). The founder wants to"
            + " know what's hot in AI today, 2026-08-29. Do a focused web research pass covering"
            + ' roughly the last 2-4 weeks.',
        )).toBe("The founder wants to know what's hot in AI today, 2026-08-29.");
    });

    it('keeps the first sentence of an ordinary brief and leaves the detail behind', () => {
        expect(readableSubagentTask('Draft the launch note. Keep it under 200 words.'))
            .toBe('Draft the launch note.');
    });

    it('returns nothing rather than a bare identifier or a serialized object', () => {
        expect(readableSubagentTask('0da4bc62-ec07-4d41-82e2-7afcc15b9efa')).toBeUndefined();
        expect(readableSubagentTask('{"task":"Draft the launch note"}')).toBeUndefined();
        expect(readableSubagentTask('   ')).toBeUndefined();
        expect(readableSubagentTask(undefined)).toBeUndefined();
    });

    it('passes through a brief that is a single unpunctuated line', () => {
        expect(readableSubagentTask('Research the tournament format'))
            .toBe('Research the tournament format');
    });
});
