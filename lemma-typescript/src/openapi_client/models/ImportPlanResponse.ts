/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorPlan } from './ConnectorPlan.js';
import type { ImportPlanOutcome } from './ImportPlanOutcome.js';
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
    /**
     * What the bundle's pod owned (`pod.json -> outcome`) and whether the import sets it on this pod, which it does only when the pod owns nothing yet. Null when the bundle's pod owned nothing, when its block is invalid (said in `warnings`), or for a template import, whose start sets it.
     */
    outcome?: (ImportPlanOutcome | null);
    steps?: Array<PlanStepResponse>;
    variables?: Array<VariableSpecResponse>;
    warnings?: Array<string>;
};
