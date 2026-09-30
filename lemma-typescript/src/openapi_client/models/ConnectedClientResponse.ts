/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Scope } from './Scope.js';
export type ConnectedClientResponse = {
    client_id: string;
    client_name: string;
    client_uri: (string | null);
    connected_at: string;
    grant_id: string;
    last_used_at: (string | null);
    pod_id: string;
    scopes: Array<Scope>;
    /**
     * The person who connected it.
     */
    user_id: string;
};
