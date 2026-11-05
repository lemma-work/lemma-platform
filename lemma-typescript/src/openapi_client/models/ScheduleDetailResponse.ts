/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ListeningResponse } from './ListeningResponse.js';
import type { ScheduleFireStatus } from './ScheduleFireStatus.js';
import type { ScheduleType } from './ScheduleType.js';
/**
 * Schedule detail response.
 */
export type ScheduleDetailResponse = {
    /**
     * Deprecated and always null; see `pod_connector`.
     * @deprecated
     */
    account_id?: (string | null);
    agent_id: (string | null);
    agent_name?: (string | null);
    allowed_actions?: Array<string>;
    config: Record<string, any>;
    connector_trigger_id: (string | null);
    consecutive_failures?: number;
    created_at: string;
    filter_instruction: (string | null);
    filter_output_schema: (Record<string, any> | null);
    id: string;
    include_outside_rows?: boolean;
    instruction?: (string | null);
    is_active: boolean;
    is_internal: boolean;
    last_error?: (string | null);
    last_fire_status?: (ScheduleFireStatus | null);
    last_fired_at?: (string | null);
    last_run_id?: (string | null);
    /**
     * For a schedule on an MCP server's event: whether it is still hearing from the server. Absent for every other schedule.
     */
    listening?: (ListeningResponse | null);
    name: (string | null);
    /**
     * TIME schedules: the next instant it fires, in UTC. Absent while paused, after a one-time schedule has fired, and for other types.
     */
    next_fire_at?: (string | null);
    /**
     * `next_fire_at` read in the schedule's `config.timezone` (UTC when it sets none), as an ISO 8601 time with its offset.
     */
    next_fire_at_local?: (string | null);
    /**
     * True when the failure breaker paused this schedule, as opposed to a person pausing it. Reactivating resets the failure count.
     */
    readonly paused_by_failures: boolean;
    /**
     * Why the platform paused this schedule: `fire_cap` (it fired more than its daily ceiling), `consecutive_failures`, `fire_failed` (a scheduled run could not be started), or another system reason. Absent while it runs, or when a person paused it. Reactivating clears it.
     */
    paused_reason?: (string | null);
    /**
     * The pod connector it listens through, by slug.
     */
    pod_connector?: (string | null);
    pod_id: (string | null);
    schedule_type: ScheduleType;
    updated_at: string;
    user_id: string;
    visibility: string;
    workflow_id: (string | null);
    workflow_name?: (string | null);
};
