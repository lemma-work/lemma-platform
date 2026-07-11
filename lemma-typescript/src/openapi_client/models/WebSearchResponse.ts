/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SearchResult } from './SearchResult.js';
/**
 * Response model for standard web search
 */
export type WebSearchResponse = {
    /**
     * Error message if the search was not successful
     */
    error?: (string | null);
    /**
     * Status message
     */
    message?: (string | null);
    /**
     * List of search results
     */
    results?: Array<SearchResult>;
    /**
     * Whether the search was successful
     */
    success: boolean;
};
