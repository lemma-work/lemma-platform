/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionRevisionResponse } from './FunctionRevisionResponse.js';
export type FunctionRevisionPromoteResponse = {
    revision: FunctionRevisionResponse;
    /**
     * True when this revision's input/output/config schemas differ from the ones that were live. The schemas move with the revision, so agents and workflows bound to the old contract may need updating.
     */
    schema_changed: boolean;
};
