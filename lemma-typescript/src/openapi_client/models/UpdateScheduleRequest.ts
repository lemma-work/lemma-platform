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
    /**
     * DATASTORE schedules only: also fire on rows people outside the pod added to an open table. Off by default. When it fires, the run is told the row's content came from outside and is untrusted.
     */
    include_outside_rows?: (boolean | null);
    instruction?: (string | null);
    is_active?: (boolean | null);
    name?: (string | null);
    visibility?: (string | null);
    workflow_name?: (string | null);
};
