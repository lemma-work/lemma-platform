/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TriageConfig } from './TriageConfig.js';
/**
 * Request to update a schedule.
 */
export type UpdateScheduleRequest = {
    agent_name?: (string | null);
    config?: (Record<string, any> | null);
    /**
     * WEBHOOK and DATASTORE schedules only: a yes/no condition, in your own words, that each event must meet to fire the schedule. It is asked as a decision (System One when configured, the system model otherwise), and every event it turns down is recorded as a FILTERED run carrying the decision's id. Refused on TIME schedules, which have no event to judge.
     */
    filter_instruction?: (string | null);
    /**
     * Optional JSON schema of fields to extract from an event the filter let through; the target reads them, with `should_proceed` and `decision_id`, as `llm_output`. Refused on TIME schedules.
     */
    filter_output_schema?: (Record<string, any> | null);
    instruction?: (string | null);
    is_active?: (boolean | null);
    name?: (string | null);
    /**
     * WEBHOOK and DATASTORE schedules only, instead of a filter: a pod decider asked about each event, and what each option of its choice question does with it -- act (wake the target now), digest (hold it for the next digest, one run for many events), ask (hold it and ask the event's owner, whose answer routes it and teaches the decider) or ignore (record it as skipped). Every declared option must be routed. Send null to remove it.
     */
    triage?: (TriageConfig | null);
    visibility?: (string | null);
    workflow_name?: (string | null);
};
