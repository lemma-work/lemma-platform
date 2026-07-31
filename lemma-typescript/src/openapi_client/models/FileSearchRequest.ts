/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileSearchScopeMode } from './FileSearchScopeMode.js';
import type { SearchMethod } from './SearchMethod.js';
export type FileSearchRequest = {
    limit?: number;
    query: string;
    scope_mode?: FileSearchScopeMode;
    /**
     * Optional folder path to scope search results.
     */
    scope_path?: (string | null);
    search_method?: SearchMethod;
};
