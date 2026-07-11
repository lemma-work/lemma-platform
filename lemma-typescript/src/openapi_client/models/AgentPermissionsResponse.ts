/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentResourcePermissionResponse } from './AgentResourcePermissionResponse.js';
export type AgentPermissionsResponse = {
    agent_id: string;
    agent_name: string;
    grants?: Array<AgentResourcePermissionResponse>;
};
