/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One spend window, as a caller outside the deployment may see it.
 *
 * **The cap is expressed as a percentage, never as an amount.** This used to
 * carry `limit_usd` and `remaining_usd`, which state a dollar allowance —
 * and a dollar allowance is a promise the product does not make. What a plan
 * includes is set per plan and may be retuned; what a given request costs
 * depends on the model it routes to. Publishing "$12.40 remaining" invites a
 * customer to plan against a number that is neither fixed nor ours to
 * guarantee, and turns any retune into a broken promise.
 *
 * What is published instead is how much of the window is gone. That is the
 * fact a caller can act on — show a meter, warn at 80%, stop starting new
 * work — and it stays true however the underlying allowance is set.
 *
 * `used_usd` and `reserved_usd` remain, and deliberately: those are what the
 * customer has actually spent, which is theirs to know. It is the *boundary*
 * that is percentage-only, not the consumption.
 *
 * The internal `UsageLimitScope` keeps its dollar fields — enforcement is done
 * in dollars, and `usage_service` reserves against them. This is the API
 * boundary, and the boundary is where the promise is made.
 */
export type UsageLimitScopeResponse = {
    allowed: boolean;
    reserved_usd: number;
    reset_at: string;
    scope: string;
    /**
     * How much of this window is consumed, as a percentage. Null means the window is uncapped, which is a different statement from 0% used. May exceed 100: a reservation can settle above what it reserved, and a caller wanting a meter should clamp it itself rather than be handed a number that has already lost the overage.
     */
    used_percent?: (number | null);
    used_usd: number;
    window_start: string;
};
