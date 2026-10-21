/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelResponse } from './ChannelResponse.js';
export type ChannelListResponse = {
    items: Array<ChannelResponse>;
    /**
     * Pass as `cursor` for the next page.
     */
    next_cursor?: (string | null);
};
