/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Configuration for Form node (user input).
 */
export type FormNodeConfig = {
    /**
     * Pod member assigned to submit this form.
     */
    assignee_pod_member_id?: (string | null);
    /**
     * Optional JMESPath expression resolving to a pod member id. Takes precedence over assignee_pod_member_id.
     */
    assignee_pod_member_id_expression?: (string | null);
    /**
     * JSON Schema for user input
     */
    input_schema: Record<string, any>;
    /**
     * UI configuration
     */
    ui_schema?: (Record<string, any> | null);
};
