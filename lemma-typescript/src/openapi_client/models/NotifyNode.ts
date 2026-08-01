/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotifyNodeConfig } from './NotifyNodeConfig.js';
/**
 * Notify node. Delivers to the member's freshest channel and always to
 * their Lemma inbox, then advances — it does not wait for a reply.
 */
export type NotifyNode = {
    config: NotifyNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
