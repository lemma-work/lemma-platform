/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DecisionRule = {
    /**
     * JMESPath condition evaluated against the run context. The first rule whose condition is truthy selects the next node. Example: `collect_input.decision == 'approved'`.
     */
    condition: string;
    next_node_id: string;
};
