/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AuthConfigCreateSchema = {
    config?: (Record<string, any> | null);
    config_source?: string;
    connector_id: string;
    /**
     * Which of the connector's kinds to install. Optional when the connector offers only one.
     */
    kind?: (string | null);
    name?: (string | null);
};
