/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AutomationKind } from './AutomationKind.js';
export type AutomationResponse = {
    /**
     * The conversation a schedule's agent ran in, for the newest fire.
     */
    conversation_id?: (string | null);
    /**
     * The workflow's or the schedule's id.
     */
    id: string;
    kind: AutomationKind;
    latest_at: string;
    /**
     * The newest workflow run, or the newest schedule fire.
     */
    latest_run_id: string;
    latest_status: string;
    name?: (string | null);
    /**
     * Runs of the workflow, or fires of the schedule, in the window.
     */
    run_count: number;
    /**
     * The workflow run to open for the newest: the workflow's own, or the one a schedule started.
     */
    workflow_run_id?: (string | null);
};
