/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelMemberResponse } from './ChannelMemberResponse.js';
export type ChannelMemberListResponse = {
    items: Array<ChannelMemberResponse>;
    /**
     * Pass as `cursor` for the next page.
     */
    next_cursor?: (string | null);
};
