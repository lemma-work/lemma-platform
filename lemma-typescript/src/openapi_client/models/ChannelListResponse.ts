/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelResponse } from './ChannelResponse.js';
export type ChannelListResponse = {
    items: Array<ChannelResponse>;
    /**
     * Pass as `before` for the next page.
     */
    next_before?: (string | null);
};
