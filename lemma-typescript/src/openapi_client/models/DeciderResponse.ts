/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeciderDefinition } from './DeciderDefinition.js';
export type DeciderResponse = {
    created_at: string;
    definition: DeciderDefinition;
    id: string;
    name: string;
    updated_at: string;
    user_id: (string | null);
    version: number;
    visibility: string;
    /**
     * Things the definition allows but that are usually a mistake.
     */
    warnings?: Array<string>;
};
