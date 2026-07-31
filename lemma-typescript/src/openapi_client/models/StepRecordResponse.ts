/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { StepStatus } from './StepStatus.js';
export type StepRecordResponse = {
    completed_at?: (string | null);
    error?: (string | null);
    node_id: string;
    output_data?: null;
    started_at: string;
    status: StepStatus;
    step_index: number;
};
