/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Resolve a value from the run context using a JMESPath expression.
 */
export type ExpressionInputBinding = {
    /**
     * When true, an expression that resolves to nothing yields null instead of failing the run.
     */
    optional?: boolean;
    type?: string;
    /**
     * JMESPath expression evaluated against the run context. Example: `start.payload.issue.key` or `collect_input.amount`. Expressions that resolve to nothing fail the run unless `optional` is set.
     */
    value: string;
};
