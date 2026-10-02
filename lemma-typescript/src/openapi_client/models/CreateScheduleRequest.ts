/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScheduleType } from './ScheduleType.js';
import type { TriageConfig } from './TriageConfig.js';
/**
 * Request to create a pod schedule.
 */
export type CreateScheduleRequest = {
    /**
     * Connected connector account used to provision provider-backed webhook schedules.
     */
    account_id?: (string | null);
    /**
     * Pod agent to wake, by name. Pass 'POD_DEFAULT' (or 'pod_default') to wake the pod's default assistant, which has no name of its own.
     */
    agent_name?: (string | null);
    config?: Record<string, any>;
    /**
     * Connector trigger id for agent WEBHOOK schedules. Do not provide this for workflow schedules; workflow WEBHOOK schedules derive it from the workflow start configuration.
     */
    connector_trigger_id?: (string | null);
    /**
     * WEBHOOK and DATASTORE schedules only: a yes/no condition, in your own words, that each event must meet to fire the schedule. It is asked as a decision (System One when configured, the system model otherwise), and every event it turns down is recorded as a FILTERED run carrying the decision's id. Refused on TIME schedules, which have no event to judge.
     */
    filter_instruction?: (string | null);
    /**
     * Optional JSON schema of fields to extract from an event the filter let through; the target reads them, with `should_proceed` and `decision_id`, as `llm_output`. Refused on TIME schedules.
     */
    filter_output_schema?: (Record<string, any> | null);
    /**
     * What the target should do when this fires, in your own words. Reaches an agent as the run's conversation instructions, layered after the agent's own. Required when targeting the default assistant, which has no standing instruction to fall back on. Distinct from filter_instruction, which decides whether to fire.
     */
    instruction?: (string | null);
    /**
     * Stable pod-scoped schedule name used for import/export upserts.
     */
    name?: (string | null);
    schedule_type: ScheduleType;
    /**
     * WEBHOOK and DATASTORE schedules only, instead of a filter: a pod decider asked about each event, and what each option of its choice question does with it -- act (wake the target now), digest (hold it for the next digest, one run for many events), ask (hold it and ask the event's owner, whose answer routes it and teaches the decider) or ignore (record it as skipped). Every declared option must be routed.
     */
    triage?: (TriageConfig | null);
    visibility?: (string | null);
    workflow_name?: (string | null);
};
