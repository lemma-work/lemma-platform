/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HarnessKind } from './HarnessKind.js';
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
export type CreateUserDaemonRuntimeProfileRequest = {
    daemon_id: string;
    default_model_name?: (string | null);
    description?: (string | null);
    harness_kind: HarnessKind;
    name: string;
    scope?: RuntimeProfileScope;
    source?: string;
};
