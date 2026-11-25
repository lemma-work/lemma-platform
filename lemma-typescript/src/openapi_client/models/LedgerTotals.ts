/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What a set of ledger rows cost, and the model tokens among them.
 */
export type LedgerTotals = {
    /**
     * Input tokens written to the provider's prompt cache.
     */
    cache_write_tokens?: number;
    /**
     * Input tokens read from the provider's prompt cache.
     */
    cached_input_tokens?: number;
    /**
     * What every recorded call cost, in US dollars, whichever provider profile paid for it. A call recorded without a price adds nothing.
     */
    cost_usd?: number;
    /**
     * Tokens sent to the model, cached ones included (cached_input_tokens says how many of these were cache reads).
     */
    input_tokens?: number;
    /**
     * Model calls recorded: one per request for a run Lemma answers itself; an Agent Host reports each turn's calls as one.
     */
    model_calls?: number;
    /**
     * Tokens the model wrote.
     */
    output_tokens?: number;
};
