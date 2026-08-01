import { describe, expect, it } from 'vitest';

import { agentAccessChanged, carriesAccess, type AgentAccessFields } from './use-agents';
import { AccessMode, ConnectorMode, type Agent } from '@/lib/types';

/**
 * The wiring an agent comes back with. The editor page round-trips exactly
 * these fields, so a save that touched nothing must compare equal.
 */
function wiredAgent(overrides: Partial<Agent> = {}): Agent {
    return {
        id: 'agent-1',
        pod_id: 'pod-1',
        user_id: 'user-1',
        name: 'triage',
        description: null,
        icon_url: null,
        agent_runtime: null,
        instruction: 'Sort the inbox.',
        input_schema: {},
        output_schema: {},
        tool_sets: [],
        toolsets: [],
        accessible_tables: [{ table_name: 'tickets', mode: AccessMode.WRITE }],
        accessible_folders: [{ folder_path: '/runbooks', mode: AccessMode.READ }],
        accessible_connectors: [{ app_name: 'slack', mode: ConnectorMode.DYNAMIC }],
        function_names: ['notify'],
        agent_names: [],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        ...overrides,
    } as Agent;
}

/** What the agent editor sends on save — always every access field. */
function savePayload(overrides: Partial<AgentAccessFields> = {}): AgentAccessFields {
    const agent = wiredAgent();
    return {
        accessible_tables: agent.accessible_tables,
        accessible_folders: agent.accessible_folders,
        accessible_connectors: agent.accessible_connectors,
        accessible_functions: agent.function_names ?? undefined,
        accessible_agents: agent.agent_names ?? undefined,
        ...overrides,
    };
}

describe('agentAccessChanged', () => {
    // The reported bug: a pod editor changing only the instruction still tripped
    // the permissions replace, which needs `agent.delete`, and got a 403 naming
    // a permission they were not exercising.
    it('is false for a save that leaves the wiring alone', () => {
        expect(agentAccessChanged(wiredAgent(), savePayload())).toBe(false);
    });

    it('ignores ordering differences between the server and the editor', () => {
        const agent = wiredAgent({
            accessible_tables: [
                { table_name: 'tickets', mode: AccessMode.WRITE },
                { table_name: 'accounts', mode: AccessMode.READ },
            ],
        });
        expect(agentAccessChanged(agent, savePayload({
            accessible_tables: [
                { table_name: 'accounts', mode: AccessMode.READ },
                { table_name: 'tickets', mode: AccessMode.WRITE },
            ],
        }))).toBe(false);
    });

    it('is true when a table is added', () => {
        expect(agentAccessChanged(wiredAgent(), savePayload({
            accessible_tables: [
                { table_name: 'tickets', mode: AccessMode.WRITE },
                { table_name: 'accounts', mode: AccessMode.READ },
            ],
        }))).toBe(true);
    });

    it('is true when a table stays but its mode narrows', () => {
        expect(agentAccessChanged(wiredAgent(), savePayload({
            accessible_tables: [{ table_name: 'tickets', mode: AccessMode.READ }],
        }))).toBe(true);
    });

    it('is true when a connector is pinned to a fixed account', () => {
        expect(agentAccessChanged(wiredAgent(), savePayload({
            accessible_connectors: [
                { app_name: 'slack', mode: ConnectorMode.FIXED, account_id: 'acct-1' },
            ],
        }))).toBe(true);
    });

    it('is true when the wiring is cleared outright', () => {
        expect(agentAccessChanged(wiredAgent(), savePayload({
            accessible_tables: [],
            accessible_folders: [],
            accessible_connectors: [],
            accessible_functions: [],
            accessible_agents: [],
        }))).toBe(true);
    });

    it('treats an untouched field as unchanged rather than as a clear', () => {
        // A visibility-only save carries no access fields at all.
        expect(agentAccessChanged(wiredAgent(), {})).toBe(false);
    });
});

describe('carriesAccess', () => {
    it('is false for a payload that never mentions access', () => {
        expect(carriesAccess({})).toBe(false);
    });

    it('is true once any access field is present, including an empty one', () => {
        expect(carriesAccess({ accessible_tables: [] })).toBe(true);
    });
});
