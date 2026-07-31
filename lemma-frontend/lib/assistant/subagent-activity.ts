import {
    isConversationRunningStatus,
    normalizeAgentToolName,
    normalizeConversationStatus,
} from 'lemma-sdk';
import type { AssistantToolInvocation } from 'lemma-sdk/react';

const SUBAGENT_LIFECYCLE_TOOLS = new Set([
    'spawn_subagent',
    'interact_subagent',
    'query_subagents',
]);

type JsonRecord = Record<string, unknown>;

export type SubagentActivityPhase =
    | 'working'
    | 'waiting'
    | 'complete'
    | 'failed'
    | 'stopped'
    | 'unknown';

export interface SubagentActivity {
    key: string;
    conversationId?: string;
    runId?: string;
    agentName?: string;
    task?: string;
    status: string;
    output?: string;
    error?: string;
}

export interface SubagentConversationSnapshot {
    id: string;
    status?: string | null;
    last_run_status?: string | null;
    title?: string | null;
    output?: unknown;
    last_run_error?: string | null;
}

function asRecord(value: unknown): JsonRecord {
    return value && typeof value === 'object' && !Array.isArray(value)
        ? value as JsonRecord
        : {};
}

function asArray(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

function firstString(record: JsonRecord, keys: string[]): string | undefined {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return undefined;
}

function primaryArgs(args: JsonRecord): JsonRecord {
    const request = asRecord(args.request);
    return Object.keys(request).length > 0 ? request : args;
}

function textFromValue(value: unknown): string | undefined {
    if (typeof value === 'string') return value.trim() || undefined;
    const record = asRecord(value);
    if (Object.keys(record).length === 0) return undefined;
    return firstString(record, ['answer', 'text', 'message', 'content', 'error'])
        || textFromValue(record.output)
        || textFromValue(record.result);
}

function taskFromInput(value: unknown): string | undefined {
    if (typeof value === 'string') return value.trim() || undefined;
    const record = asRecord(value);
    const direct = firstString(record, ['task', 'prompt', 'message', 'instruction']);
    if (direct) return direct;
    if (typeof record.input !== 'undefined') return taskFromInput(record.input);
    if (Object.keys(record).length === 0) return undefined;
    try {
        return JSON.stringify(record);
    } catch {
        return undefined;
    }
}

function normalizedStatus(status: unknown, fallback = 'PENDING'): string {
    return normalizeConversationStatus(status) || fallback;
}

function resultChildren(result: JsonRecord): JsonRecord[] {
    const rows = result.children ?? result.conversations ?? result.items ?? result.subagents;
    return asArray(rows).map(asRecord).filter((row) => Object.keys(row).length > 0);
}

export function isSubagentLifecycleToolName(toolName: string): boolean {
    return SUBAGENT_LIFECYCLE_TOOLS.has(normalizeAgentToolName(toolName).toLowerCase());
}

export function subagentActivityPhase(status: unknown, error?: string): SubagentActivityPhase {
    if (error) return 'failed';
    const normalized = normalizedStatus(status, 'UNKNOWN');
    if (['FAILED', 'ERROR', 'CANCELLED', 'CANCELED'].includes(normalized)) return 'failed';
    if (['STOPPED', 'ABORTED'].includes(normalized)) return 'stopped';
    if (normalized === 'WAITING') return 'waiting';
    if (
        isConversationRunningStatus(normalized)
        || ['QUEUED', 'PENDING', 'STARTING'].includes(normalized)
    ) {
        return 'working';
    }
    if (['COMPLETED', 'COMPLETE', 'SUCCEEDED', 'SUCCESS', 'DONE'].includes(normalized)) {
        return 'complete';
    }
    return 'unknown';
}

export function deriveSubagentActivities(
    invocations: AssistantToolInvocation[],
): SubagentActivity[] {
    const activities: SubagentActivity[] = [];
    const byConversationId = new Map<string, SubagentActivity>();
    const byRunId = new Map<string, SubagentActivity>();

    const add = (activity: SubagentActivity) => {
        activities.push(activity);
        if (activity.conversationId) byConversationId.set(activity.conversationId, activity);
        if (activity.runId) byRunId.set(activity.runId, activity);
        return activity;
    };

    const findOrAdd = (conversationId?: string, runId?: string) => {
        const existing = (conversationId ? byConversationId.get(conversationId) : undefined)
            || (runId ? byRunId.get(runId) : undefined);
        if (existing) return existing;
        return add({
            key: conversationId || runId || `subagent-${activities.length + 1}`,
            conversationId,
            runId,
            status: 'PENDING',
        });
    };

    invocations.forEach((invocation) => {
        const toolName = normalizeAgentToolName(invocation.toolName).toLowerCase();
        if (!SUBAGENT_LIFECYCLE_TOOLS.has(toolName)) return;

        const args = primaryArgs(asRecord(invocation.args));
        const result = asRecord(invocation.result);
        const conversationId = firstString(result, ['conversation_id', 'conversationId'])
            || firstString(args, ['conversation_id', 'conversationId']);
        const runId = firstString(result, ['run_id', 'runId'])
            || firstString(args, ['run_id', 'runId']);

        if (toolName === 'spawn_subagent') {
            const success = result.success !== false;
            const activity = add({
                key: conversationId || invocation.toolCallId,
                conversationId,
                runId,
                agentName: firstString(args, ['agent_name', 'agentName']),
                task: taskFromInput(args.input),
                status: normalizedStatus(
                    result.status,
                    invocation.state === 'result' ? (success ? 'RUNNING' : 'FAILED') : 'PENDING',
                ),
                output: textFromValue(result.output),
                error: success ? undefined : firstString(result, ['error', 'message']),
            });
            if (activity.conversationId) byConversationId.set(activity.conversationId, activity);
            if (activity.runId) byRunId.set(activity.runId, activity);
            return;
        }

        if (toolName === 'interact_subagent') {
            const activity = findOrAdd(conversationId, runId);
            const action = firstString(args, ['action'])?.toLowerCase();
            const success = result.success !== false;
            activity.conversationId ||= conversationId;
            activity.runId ||= runId;
            activity.status = normalizedStatus(
                result.status,
                !success ? 'FAILED' : action === 'stop' ? 'STOPPED' : action === 'send' ? 'RUNNING' : activity.status,
            );
            activity.output = textFromValue(result.output) || activity.output;
            activity.error = !success
                ? firstString(result, ['error', 'message']) || activity.error
                : firstString(result, ['error']) || activity.error;
            if (activity.conversationId) byConversationId.set(activity.conversationId, activity);
            if (activity.runId) byRunId.set(activity.runId, activity);
            return;
        }

        const mode = firstString(args, ['mode'])?.toLowerCase();
        if (mode !== 'list') return;
        resultChildren(result).forEach((child) => {
            const childConversationId = firstString(child, ['conversation_id', 'conversationId', 'id']);
            const childRunId = firstString(child, ['run_id', 'runId']);
            const activity = findOrAdd(childConversationId, childRunId);
            activity.conversationId ||= childConversationId;
            activity.runId ||= childRunId;
            activity.agentName ||= firstString(child, ['agent_name', 'agentName']);
            activity.task ||= firstString(child, ['title', 'task']);
            activity.status = normalizedStatus(child.status, activity.status);
            if (activity.conversationId) byConversationId.set(activity.conversationId, activity);
            if (activity.runId) byRunId.set(activity.runId, activity);
        });
    });

    return activities;
}

export function mergeSubagentConversationSnapshots(
    activities: SubagentActivity[],
    snapshots: SubagentConversationSnapshot[],
): SubagentActivity[] {
    const merged = activities.map((activity) => ({ ...activity }));
    const byConversationId = new Map(
        merged
            .filter((activity) => activity.conversationId)
            .map((activity) => [activity.conversationId as string, activity]),
    );

    snapshots.forEach((snapshot) => {
        let activity = byConversationId.get(snapshot.id);
        if (!activity) {
            activity = {
                key: snapshot.id,
                conversationId: snapshot.id,
                status: normalizedStatus(snapshot.last_run_status ?? snapshot.status),
            };
            merged.push(activity);
            byConversationId.set(snapshot.id, activity);
        }

        activity.task ||= snapshot.title?.trim() || undefined;
        activity.status = normalizedStatus(
            snapshot.last_run_status ?? snapshot.status,
            activity.status,
        );
        activity.output = textFromValue(snapshot.output) || activity.output;
        activity.error = snapshot.last_run_error?.trim() || activity.error;
    });

    return merged;
}

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
    return `${count} ${count === 1 ? singular : plural}`;
}

export function summarizeSubagentActivities(activities: SubagentActivity[]): string {
    const counts = new Map<SubagentActivityPhase, number>();
    activities.forEach((activity) => {
        const phase = subagentActivityPhase(activity.status, activity.error);
        counts.set(phase, (counts.get(phase) ?? 0) + 1);
    });

    const phrases = [
        counts.get('working') ? `${counts.get('working')} working` : null,
        counts.get('waiting') ? `${counts.get('waiting')} waiting` : null,
        counts.get('complete') ? `${counts.get('complete')} complete` : null,
        counts.get('failed') ? `${counts.get('failed')} failed` : null,
        counts.get('stopped') ? `${counts.get('stopped')} stopped` : null,
    ].filter((phrase): phrase is string => Boolean(phrase));

    const total = countLabel(activities.length, 'sub-agent');
    return phrases.length > 0 ? `${total} · ${phrases.join(' · ')}` : total;
}
