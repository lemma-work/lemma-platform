/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WebLoginResponse } from './WebLoginResponse.js';
export type WebLoginListResponse = {
    items: Array<WebLoginResponse>;
    /**
     * True when the computer is paused and was not woken to answer. Items are empty; its browser still holds whatever it held.
     */
    sleeping?: boolean;
};
