/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceMeApp } from './WorkspaceMeApp.js';
import type { WorkspaceMeSandbox } from './WorkspaceMeSandbox.js';
import type { WorkspaceMeSession } from './WorkspaceMeSession.js';
export type WorkspaceMeResponse = {
    active_session?: (WorkspaceMeSession | null);
    apps: Record<string, WorkspaceMeApp>;
    sandbox: WorkspaceMeSandbox;
    user_id: string;
};
