/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorPlan } from './ConnectorPlan.js';
import type { PlanStepResponse } from './PlanStepResponse.js';
import type { VariableSpecResponse } from './VariableSpecResponse.js';
export type ImportPlanResponse = {
    bundle_name?: (string | null);
    /**
     * The bundle's pod connectors, each with the importing org's installations of its catalog connector, whether one can be created, and (SHARED) your own accounts you could share. NEEDS_INSTALLATION and CONFLICT block the apply until resolved; NEEDS_ACCOUNT imports, waiting for an account.
     */
    connectors?: Array<ConnectorPlan>;
    format_version: number;
    has_destructive_steps?: boolean;
    steps?: Array<PlanStepResponse>;
    variables?: Array<VariableSpecResponse>;
    warnings?: Array<string>;
};
