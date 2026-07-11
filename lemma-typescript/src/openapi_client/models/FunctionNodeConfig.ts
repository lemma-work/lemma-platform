/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExpressionInputBinding } from './ExpressionInputBinding.js';
import type { LiteralInputBinding } from './LiteralInputBinding.js';
/**
 * Configuration for Function node.
 */
export type FunctionNodeConfig = {
    /**
     * Function resource name to execute.
     */
    function_name: string;
    /**
     * Explicit mapping from function argument key to either an expression or a literal JSON value. Strings are never auto-interpreted.
     */
    input_mapping?: Record<string, (ExpressionInputBinding | LiteralInputBinding)>;
};
