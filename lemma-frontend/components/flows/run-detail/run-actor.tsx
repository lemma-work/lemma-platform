'use client';

// Who did this step.
//
// From the README: generated code "still needs shared state, permissions,
// workflows, approvals… People use the app; agents work through the same state
// and workflows." A workflow is not a script — it is the thing that moves work
// between people, agents and functions, pausing for approval and surviving the
// wait. That is why it exists, and it is the only reason it is not just an
// agent conversation.
//
// The flows index already reads a workflow this way: FORM → human, AGENT → ai,
// FUNCTION → function, everything else → the system. This is the same mapping,
// resolved down to an actual name — the agent that ran, the function that was
// called, the person a form is waiting on — because "who has this" is the first
// question anybody asks about a run and node types cannot answer it.

import { Bot, Cog, UserRound, Zap } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { NodeType, type WorkflowNode } from '@/lib/types';
import { getNodeAgentName } from '../run-format';
import { getFunctionNodeName } from '@/lib/utils/flow-node-config';

export type ActorKind = 'human' | 'agent' | 'function' | 'system';

export type RunActor = {
    kind: ActorKind;
    /** The actor's own name — an agent, a function, a person. */
    name: string;
    /** What kind of participant that is, for the secondary slot. */
    role: string;
};

/** The icon treatment every index row in the product uses — see WorkflowSteps in
 * run-cards.tsx, which lists these same steps on the workflow page. */
const ACTOR_ICONS: Record<ActorKind, typeof Bot> = {
    human: UserRound,
    agent: Bot,
    function: Zap,
    system: Cog,
};

export function getRunActor(node: WorkflowNode | null | undefined, stepLabel: string): RunActor {
    if (!node) return { kind: 'system', name: stepLabel, role: 'step' };

    if (node.type === NodeType.AGENT) {
        return {
            kind: 'agent',
            name: getNodeAgentName(node) || stepLabel,
            role: 'agent',
        };
    }

    if (node.type === NodeType.FUNCTION) {
        const fn = getFunctionNodeName((node.config || {}) as Record<string, unknown>);
        return { kind: 'function', name: fn || stepLabel, role: 'function' };
    }

    if (node.type === NodeType.FORM) {
        return { kind: 'human', name: stepLabel, role: 'a person' };
    }

    if (node.type === NodeType.DECISION) {
        return { kind: 'system', name: stepLabel, role: 'decision' };
    }

    if (node.type === NodeType.LOOP) {
        return { kind: 'system', name: stepLabel, role: 'repeats' };
    }

    if (node.type === NodeType.WAIT_UNTIL) {
        return { kind: 'system', name: stepLabel, role: 'waits' };
    }

    if (node.type === NodeType.END) {
        return { kind: 'system', name: stepLabel, role: 'end' };
    }

    return { kind: 'system', name: stepLabel, role: 'step' };
}

export function ActorAvatar({ kind, className }: { kind: ActorKind; className?: string }) {
    const Icon = ACTOR_ICONS[kind];
    return (
        <span
            className={cn(
                'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]',
                className
            )}
            aria-hidden
        >
            <Icon className="h-4 w-4" />
        </span>
    );
}
