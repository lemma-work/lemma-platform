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
     * Set when the search could not be run exactly as asked — for example a vertical this provider does not serve.
     */
    note?: (string | null);
    /**
     * List of search results
     */
    results?: Array<SearchResult>;
    /**
     * Whether the search was successful
     */
    success: boolean;
};
