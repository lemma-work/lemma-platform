/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScheduleType } from './ScheduleType.js';
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
     * Optional schedule-level LLM filter instruction. Filters belong to the schedule, not the workflow start.
     */
    filter_instruction?: (string | null);
    /**
     * Optional schema for the schedule-level filter output. Filters belong to the schedule, not the workflow start.
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
    visibility?: (string | null);
    workflow_name?: (string | null);
};
