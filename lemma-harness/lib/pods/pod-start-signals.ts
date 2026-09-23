import { isPodDefaultAgentName } from '@/lib/utils/agents';
import {
    resolvePodHomeStarterMode,
    type PodHomeResourceSignals,
    type PodHomeStarterMode,
} from './pod-home-starters';

// What the new-conversation screen knows about the pod it is starting work in.
//
// Home answers "what is happening in this pod"; a new conversation answers the
// narrower question "what can I ask for right now". Both read the same
// resources, so the mode stays shared with pod-home-starters — a pod that reads
// as `operating` on Home must not read as `fresh` the moment you open a tab.

export type PodStartMode = PodHomeStarterMode;

export interface PodStartTable {
    name: string;
}

export interface PodStartWorkflow {
    name: string;
}

export interface PodStartAgent {
    name: string;
    iconUrl?: string | null;
}

export interface PodStartConnector {
    connectorId: string;
    label: string;
    icon?: string | null;
}

export interface PodStartSignals {
    tables: PodStartTable[];
    agents: PodStartAgent[];
    workflows: PodStartWorkflow[];
    connectors: PodStartConnector[];
    appCount: number;
    surfaceCount: number;
    activeSurfaceCount: number;
    scheduleCount: number;
    conversationCount: number;
    hasUsedWorkflow: boolean;
}

export interface PodStartAction {
    id: string;
    label: string;
    /**
     * A complete instruction. Build verbs may hand the composer a fragment to
     * finish, but an action derived from something the pod already has should
     * be sendable on its own — one click is meant to be real work.
     */
    prompt: string;
}

export const EMPTY_POD_START_SIGNALS: PodStartSignals = {
    tables: [],
    agents: [],
    workflows: [],
    connectors: [],
    appCount: 0,
    surfaceCount: 0,
    activeSurfaceCount: 0,
    scheduleCount: 0,
    conversationCount: 0,
    hasUsedWorkflow: false,
};

const MAX_FACTS = 4;
const MAX_DO_ACTIONS = 8;
const MAX_TABLE_ACTIONS = 4;
const MAX_WORKFLOW_ACTIONS = 4;

function toHomeSignals(signals: PodStartSignals): PodHomeResourceSignals {
    return {
        appCount: signals.appCount,
        // The pod's own assistant is in every pod from the moment it exists, so
        // counting it makes this signal a constant: a brand-new pod would report
        // an agent and never read as fresh again.
        agentCount: signals.agents.filter((agent) => !isPodDefaultAgentName(agent.name)).length,
        workflowCount: signals.workflows.length,
        surfaceCount: signals.surfaceCount,
        activeSurfaceCount: signals.activeSurfaceCount,
        scheduleCount: signals.scheduleCount,
        conversationCount: signals.conversationCount,
        hasUsedWorkflow: signals.hasUsedWorkflow,
    };
}

export function resolvePodStartMode(signals: PodStartSignals): PodStartMode {
    return resolvePodHomeStarterMode(toHomeSignals(signals));
}

function countFact(count: number, singular: string, plural: string): string | null {
    if (count <= 0) return null;
    return `${count} ${count === 1 ? singular : plural}`;
}

/**
 * The one-line answer to "what is in this pod" — capped, because a capability
 * line that runs past a few facts stops being read at all.
 */
export function buildPodFacts(signals: PodStartSignals): string[] {
    return [
        countFact(signals.tables.length, 'table', 'tables'),
        countFact(signals.agents.length, 'agent', 'agents'),
        countFact(signals.workflows.length, 'workflow', 'workflows'),
        countFact(signals.appCount, 'app', 'apps'),
        countFact(signals.activeSurfaceCount, 'live surface', 'live surfaces'),
        countFact(signals.scheduleCount, 'schedule', 'schedules'),
    ]
        .filter((fact): fact is string => fact !== null)
        .slice(0, MAX_FACTS);
}

function titleCase(value: string): string {
    return value
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .split(' ')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}

/**
 * Actions built from what the pod already has. Empty for a pod with nothing in
 * it — that case belongs to the build verbs, not to a fabricated suggestion.
 *
 * Tables and workflows keep catalog order. The table list carries no row count
 * or last-write time, so there is nothing here to rank on honestly; catalog
 * order at least matches what the Data page shows first, and stays put between
 * visits.
 */
export function buildPodDoActions(signals: PodStartSignals): PodStartAction[] {
    const tableActions = signals.tables
        .slice(0, MAX_TABLE_ACTIONS)
        .map((table) => ({
            id: `table:${table.name}`,
            label: `Review ${titleCase(table.name)}`,
            prompt: `Summarize what's in the ${table.name} table and flag anything that needs attention.`,
        }));

    const workflowActions = signals.workflows
        .slice(0, MAX_WORKFLOW_ACTIONS)
        .map((workflow) => ({
            id: `workflow:${workflow.name}`,
            label: `Run ${titleCase(workflow.name)}`,
            prompt: `Run the ${workflow.name} workflow and tell me what it did.`,
        }));

    // Interleave so a pod with many tables and one workflow still surfaces the
    // workflow — the point is coverage of what the pod can do, not a top list
    // of one resource kind.
    const interleaved: PodStartAction[] = [];
    for (let index = 0; index < Math.max(tableActions.length, workflowActions.length); index += 1) {
        if (tableActions[index]) interleaved.push(tableActions[index]);
        if (workflowActions[index]) interleaved.push(workflowActions[index]);
    }

    return interleaved.slice(0, MAX_DO_ACTIONS);
}
