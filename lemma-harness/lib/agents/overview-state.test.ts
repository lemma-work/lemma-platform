import { describe, expect, it } from 'vitest';

import { getAgentOverviewState, type AgentOverviewInputs } from './overview-state';

const untouched: AgentOverviewInputs = {
    surfaceCount: 0,
    scheduleCount: 0,
    conversationCount: 0,
    canUseSurfaces: true,
    canUseSchedules: true,
    canCreateSchedule: true,
};

describe('getAgentOverviewState', () => {
    it('treats an unreachable, unused agent as a draft', () => {
        expect(getAgentOverviewState(untouched)).toBe('draft');
    });

    it('leaves draft as soon as anything can reach it', () => {
        expect(getAgentOverviewState({ ...untouched, surfaceCount: 1 })).toBe('live');
        expect(getAgentOverviewState({ ...untouched, scheduleCount: 1 })).toBe('live');
    });

    it('leaves draft once it has been used, even with nothing wired up', () => {
        // Someone has been messaging it by hand. It works; do not open on a
        // setup screen. The rail still says it is not reachable.
        expect(getAgentOverviewState({ ...untouched, conversationCount: 1 })).toBe('live');
    });

    it('ignores tool count entirely', () => {
        // Not an input at all — an instruction-only agent is finished, and the
        // shape of this type is the guarantee that nobody reintroduces it.
        expect(Object.keys(untouched)).not.toContain('toolCount');
    });

    it('stays live when the viewer can do nothing about the setup', () => {
        expect(getAgentOverviewState({
            ...untouched,
            canUseSurfaces: false,
            canUseSchedules: false,
            canCreateSchedule: false,
        })).toBe('live');
    });

    it('offers the draft screen when only channels are available', () => {
        expect(getAgentOverviewState({
            ...untouched,
            canUseSchedules: false,
            canCreateSchedule: false,
        })).toBe('draft');
    });

    it('offers the draft screen when only triggers are available', () => {
        expect(getAgentOverviewState({ ...untouched, canUseSurfaces: false })).toBe('draft');
    });

    it('stays live when schedules are readable but not creatable', () => {
        // Seeing the trigger list is not the same as being able to add one, and
        // a setup screen whose only action is unavailable is a dead end.
        expect(getAgentOverviewState({
            ...untouched,
            canUseSurfaces: false,
            canCreateSchedule: false,
        })).toBe('live');
    });
});
