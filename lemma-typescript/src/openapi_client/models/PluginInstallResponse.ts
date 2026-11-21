/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PluginInstallItemResponse } from './PluginInstallItemResponse.js';
/**
 * A plugin install: every plugin it named, in order.
 */
export type PluginInstallResponse = {
    /**
     * Every plugin has stopped moving: INSTALLED, NEEDS_ANSWER, FAILED or CANCELLED. The Lemma tells the person who started it in their chat with it when this turns true.
     */
    finished: boolean;
    install_id: string;
    plugins: Array<PluginInstallItemResponse>;
    pod_id: string;
};
