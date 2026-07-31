/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to update a schedule.
 */
export type UpdateScheduleRequest = {
    agent_name?: (string | null);
    config?: (Record<string, any> | null);
    filter_instruction?: (string | null);
    filter_output_schema?: (Record<string, any> | null);
    is_active?: (boolean | null);
    name?: (string | null);
    visibility?: (string | null);
    workflow_name?: (string | null);
};
