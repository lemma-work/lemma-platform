/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PluginSourceRequest } from './PluginSourceRequest.js';
/**
 * Body for installing several plugins into one Lemma.
 */
export type PluginInstallRequest = {
    /**
     * The plugins to install, in the order to install them. Each counts as one import against the daily import limit.
     */
    plugins: Array<PluginSourceRequest>;
};
