'use client';

import { useState } from 'react';

import { AgentAccessDialog } from '@/components/agents/agent-access-dialog';
import { AgentContractDialog } from '@/components/agents/agent-contract-dialog';
import { Nothing, WiringRow } from '@/components/pod/wiring-row';
import { TriggersRow } from '@/components/triggers/triggers-row';
import { AgentSurfacesRow } from '@/components/surfaces/agent-surfaces-row';
import { Button } from '@/components/ui/button';
import type { Agent, AssistantSurface, Schedule } from '@/lib/types';

/**
 * How this agent is wired, as four questions with one-line answers.
 *
 * Each row states what is *true* and offers one verb. The page used to answer
 * these in two different modes — what it can reach in the editor, who can reach
 * it in the overview — and to spell an empty agent out as six separate rows of
 * "No X". Absence is worth one line, not six.
 */

function schemaKeys(schema: unknown): string[] {
    if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [];
    const properties = (schema as { properties?: unknown }).properties;
    if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return [];
    return Object.keys(properties);
}

function countPhrase(count: number, noun: string): string | null {
    if (count <= 0) return null;
    return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

/** What the agent can reach, as a sentence rather than an inventory. */
export function describeAccess(agent: Agent): string | null {
    const connectors = agent.accessible_connectors || [];
    const named = connectors.slice(0, 2).map((entry) => entry.app_name);
    const connectorPhrase = connectors.length === 0
        ? null
        : connectors.length <= 2
            ? named.join(', ')
            : `${named.join(', ')} +${connectors.length - 2}`;

    const parts = [
        countPhrase(agent.tool_sets?.length || 0, 'tool'),
        connectorPhrase,
        countPhrase(agent.accessible_tables?.length || 0, 'table'),
        countPhrase(agent.accessible_folders?.length || 0, 'folder'),
        countPhrase(agent.function_names?.length || 0, 'function'),
        countPhrase(agent.agent_names?.length || 0, 'agent'),
    ].filter(Boolean);

    return parts.length > 0 ? parts.join(' · ') : null;
}

export function AgentWiringRows({
    podId,
    agent,
    onUpdate,
    canEdit,
    surfaces,
    schedules,
    canUseSurfaces,
    canUseSchedules,
    canCreateSchedule,
    canUpdateSchedule,
    canDeleteSchedule,
}: {
    podId: string;
    agent: Agent;
    onUpdate: (data: Partial<Agent>) => void;
    canEdit: boolean;
    surfaces: AssistantSurface[];
    schedules: Schedule[];
    canUseSurfaces: boolean;
    canUseSchedules: boolean;
    canCreateSchedule: boolean;
    canUpdateSchedule: boolean;
    canDeleteSchedule: boolean;
}) {
    const [isAccessOpen, setIsAccessOpen] = useState(false);
    const [isContractOpen, setIsContractOpen] = useState(false);

    const access = describeAccess(agent);
    const takes = schemaKeys(agent.input_schema);
    const returns = schemaKeys(agent.output_schema);

    return (
        <section className="agent-wiring" data-edu="agent-access">
            <WiringRow
                label="Can use"
                action={canEdit ? (
                    <Button type="button" variant="secondary" size="sm" onClick={() => setIsAccessOpen(true)}>
                        Manage
                    </Button>
                ) : null}
            >
                {access ? <span className="agent-wiring-text">{access}</span> : <Nothing>Nothing — it works from its instructions alone.</Nothing>}
            </WiringRow>

            {canUseSurfaces ? (
                <WiringRow label="Reached by">
                    <div className="agent-wiring-chips">
                        {surfaces.length === 0 ? <Nothing>Only you, here.</Nothing> : null}
                        <AgentSurfacesRow podId={podId} agentName={agent.name} surfaces={surfaces} label={null} />
                    </div>
                </WiringRow>
            ) : null}

            {canUseSchedules ? (
                <TriggersRow
                    podId={podId}
                    target={{ kind: 'agent', name: agent.name }}
                    schedules={schedules}
                    canCreate={canCreateSchedule}
                    canUpdate={canUpdateSchedule}
                    canDelete={canDeleteSchedule}
                    emptyText="You ask it to."
                />
            ) : null}

            <div data-edu="agent-variables">
                <WiringRow
                    label="Takes"
                    action={canEdit ? (
                        <Button type="button" variant="secondary" size="sm" onClick={() => setIsContractOpen(true)}>
                            Edit
                        </Button>
                    ) : null}
                >
                    {takes.length === 0 && returns.length === 0 ? (
                        <Nothing>Anything you say; answers in prose.</Nothing>
                    ) : (
                        <span className="agent-wiring-text">
                            {takes.length > 0 ? takes.join(', ') : 'anything'}
                            <span className="agent-wiring-arrow"> → </span>
                            {returns.length > 0 ? returns.join(', ') : 'prose'}
                        </span>
                    )}
                </WiringRow>
            </div>

            {canEdit ? (
                <>
                    <AgentAccessDialog
                        open={isAccessOpen}
                        onOpenChange={setIsAccessOpen}
                        agent={agent}
                        onUpdate={onUpdate}
                    />
                    <AgentContractDialog
                        open={isContractOpen}
                        onOpenChange={setIsContractOpen}
                        agent={agent}
                        onUpdate={onUpdate}
                    />
                </>
            ) : null}
        </section>
    );
}
