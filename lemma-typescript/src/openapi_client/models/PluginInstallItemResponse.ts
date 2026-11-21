/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportProgressResponse } from './ExportProgressResponse.js';
import type { PluginInstallState } from './PluginInstallState.js';
import type { PluginInstallStepResponse } from './PluginInstallStepResponse.js';
/**
 * Where one plugin of an install is.
 */
export type PluginInstallItemResponse = {
    /**
     * The app it put in the Lemma, once INSTALLED, if it brings one.
     */
    app?: (string | null);
    /**
     * Where to connect the account a failure names, if it names one.
     */
    connect_url?: (string | null);
    /**
     * The step running now, if one is.
     */
    current_step?: (PluginInstallStepResponse | null);
    error_code?: (string | null);
    /**
     * Its import: GET /pods/{pod_id}/bundle/imports/{import_id} has its whole plan and every step.
     */
    import_id: string;
    /**
     * The bundle's own name once planned, else its folder's.
     */
    name: string;
    path?: (string | null);
    /**
     * Its place in the request, from 0.
     */
    position: number;
    /**
     * Plan steps done (or skipped) of all its steps; 0/0 before its plan.
     */
    progress?: ExportProgressResponse;
    /**
     * Why it stopped: the question its page asks (NEEDS_ANSWER), or what failed (FAILED).
     */
    reason?: (string | null);
    ref?: (string | null);
    repo_url: string;
    /**
     * WAITING behind the plugins before it; INSTALLING; INSTALLED; NEEDS_ANSWER -- stopped before anything was applied, because its install page would ask the question in `reason`; FAILED -- stopped, and POST …/retry continues it from the failed step; CANCELLED by somebody.
     */
    state: PluginInstallState;
    warnings?: Array<string>;
};
