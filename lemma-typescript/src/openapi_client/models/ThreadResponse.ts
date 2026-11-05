/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelMessageResponse } from './ChannelMessageResponse.js';
export type ThreadResponse = {
    /**
     * Whether there are more replies in the direction read: newer ones, or with `before_seq` or `newest`, older ones.
     */
    has_more: boolean;
    /**
     * Replies, oldest first.
     */
    items: Array<ChannelMessageResponse>;
    /**
     * Pass as `after_seq` for the newer page.
     */
    next_after_seq?: (number | null);
    /**
     * Reading backwards: pass as `before_seq` for the older page.
     */
    next_before_seq?: (number | null);
    root: ChannelMessageResponse;
};
