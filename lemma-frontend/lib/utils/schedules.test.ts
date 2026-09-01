import { describe, expect, it } from 'vitest';

import { getScheduleTargetName } from './schedules';
import { DEFAULT_RESPONDER_NAME } from './agents';

import type { Schedule } from '@/lib/types';

const schedule = (fields: Partial<Schedule>) => fields as Schedule;

describe('getScheduleTargetName', () => {
    // `POD_DEFAULT` is the selector the API takes and echoes back. It is not
    // the row's name and not a display name, so it must never reach a reader —
    // this is the one place that translation happens.
    it('shows the assistant by the name people know it by', () => {
        expect(getScheduleTargetName(schedule({ agent_name: 'POD_DEFAULT' })))
            .toBe(DEFAULT_RESPONDER_NAME);
    });

    it('shows a named agent by its own name', () => {
        expect(getScheduleTargetName(schedule({ agent_name: 'triage' }))).toBe('triage');
    });

    it('prefers a workflow when the schedule targets one', () => {
        expect(getScheduleTargetName(schedule({ workflow_name: 'digest' }))).toBe('digest');
    });

    it('says so rather than rendering nothing when a target cannot be named', () => {
        expect(getScheduleTargetName(schedule({}))).toBe('Unknown target');
    });
});
