/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonValue } from './JsonValue.js';
import type { ScheduleType } from './ScheduleType.js';
/**
 * An MCP Events descriptor, plus the schedule type that serves it.
 */
export type EventDescriptorResponse = {
    description: string;
    event?: (string | null);
    input_schema: Record<string, JsonValue>;
    name: string;
    payload_schema: Record<string, JsonValue>;
    pod_connector?: (string | null);
    schedule_type: ScheduleType;
    server?: (string | null);
    title: string;
};
