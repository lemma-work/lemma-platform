/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanStepResponse } from './PlanStepResponse.js';
import type { VariableSpecResponse } from './VariableSpecResponse.js';
export type ImportPlanResponse = {
    bundle_name?: (string | null);
    format_version: number;
    has_destructive_steps?: boolean;
    steps?: Array<PlanStepResponse>;
    variables?: Array<VariableSpecResponse>;
    warnings?: Array<string>;
};

