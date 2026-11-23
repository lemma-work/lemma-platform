/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Model tokens one run used, summed over its model calls.
 */
export type RunTokenUsage = {
    /**
     * Input tokens written to the provider's prompt cache.
     */
    cache_write_tokens?: number;
    /**
     * Input tokens read from the provider's prompt cache.
     */
    cached_input_tokens?: number;
    /**
     * Tokens sent to the model, cached ones included (cached_input_tokens says how many of these were cache reads).
     */
    input_tokens?: number;
    /**
     * Every model the run's calls were recorded against, by name.
     */
    models?: Array<string>;
    /**
     * Tokens the model wrote.
     */
    output_tokens?: number;
};
