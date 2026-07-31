/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppStatus } from './AppStatus.js';
export type AppDetailResponse = {
    allowed_actions?: Array<string>;
    created_at: any;
    current_release_id?: (string | null);
    description?: (string | null);
    id: string;
    name: string;
    pod_id: string;
    public_slug: string;
    source_archive_path?: (string | null);
    status: AppStatus;
    updated_at: any;
    readonly url: string;
    user_id: string;
    visibility?: string;
};
