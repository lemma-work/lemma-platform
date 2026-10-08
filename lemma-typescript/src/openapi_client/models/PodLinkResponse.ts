/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SharedResourceBody } from './SharedResourceBody.js';
export type PodLinkResponse = {
    description?: (string | null);
    icon_url?: (string | null);
    name: string;
    /**
     * The other pod.
     */
    pod_id: string;
    shared: Array<SharedResourceBody>;
    steward_name?: (string | null);
    /**
     * Who connected it, and looks after it.
     */
    steward_user_id?: (string | null);
};
