/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SlackAppWorkspaceResponse } from './SlackAppWorkspaceResponse.js';
export type SlackAppWorkspaceListResponse = {
    /**
     * Whether the caller may save tokens and create apps: an owner or editor of the organization. Others are shown no workspace.
     */
    can_manage: boolean;
    items: Array<SlackAppWorkspaceResponse>;
};
