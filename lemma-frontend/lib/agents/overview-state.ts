/**
 * Which overview an agent gets.
 *
 * The two states answer different questions. A draft agent's page exists to
 * finish setting it up; a working agent's page exists to use it and see what it
 * has been doing. Serving one layout to both is what left the page a flat stack
 * of equally-weighted rows.
 */

export type AgentOverviewState = 'draft' | 'live';

export type AgentOverviewInputs = {
    surfaceCount: number;
    scheduleCount: number;
    conversationCount: number;
    canUseSurfaces: boolean;
    canUseSchedules: boolean;
    canCreateSchedule: boolean;
};

/**
 * `draft` only when nothing can reach the agent, nothing wakes it, and nobody
 * has used it — and the viewer can actually do something about that.
 *
 * Tool count is deliberately absent. An agent that is pure instruction is a
 * perfectly finished agent, and treating zero tools as unfinished would nag the
 * people who meant it. What is genuinely incomplete is reachability: no channel
 * and no trigger means the agent runs only when its owner opens this page and
 * types. That is worth leading with, once.
 *
 * A viewer who can neither connect surfaces nor create schedules gets `live`
 * regardless — a setup screen offering nothing it can do is worse than the
 * working layout, which at least reports the state honestly in its rail.
 */
export function getAgentOverviewState({
    surfaceCount,
    scheduleCount,
    conversationCount,
    canUseSurfaces,
    canUseSchedules,
    canCreateSchedule,
}: AgentOverviewInputs): AgentOverviewState {
    const untouched = surfaceCount === 0 && scheduleCount === 0 && conversationCount === 0;
    const canFinishSetup = canUseSurfaces || (canUseSchedules && canCreateSchedule);
    return untouched && canFinishSetup ? 'draft' : 'live';
}
