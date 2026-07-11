/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScheduleFireStatus } from './ScheduleFireStatus.js';
import type { ScheduleType } from './ScheduleType.js';
/**
 * Schedule detail response.
 */
export type ScheduleDetailResponse = {
    account_id: (string | null);
    agent_id: (string | null);
    agent_name?: (string | null);
    allowed_actions?: Array<string>;
    config: Record<string, any>;
    connector_trigger_id: (string | null);
    created_at: string;
    filter_instruction: (string | null);
    filter_output_schema: (Record<string, any> | null);
    id: string;
    is_active: boolean;
    is_internal: boolean;
    last_error?: (string | null);
    last_fire_status?: (ScheduleFireStatus | null);
    last_fired_at?: (string | null);
    last_run_id?: (string | null);
    name: (string | null);
    pod_id: (string | null);
    schedule_type: ScheduleType;
    updated_at: string;
    user_id: string;
    visibility: string;
    workflow_id: (string | null);
    workflow_name?: (string | null);
};
