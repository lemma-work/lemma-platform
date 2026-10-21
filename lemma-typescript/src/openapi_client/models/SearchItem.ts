/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelMessageResponse } from './ChannelMessageResponse.js';
import type { ChannelRef } from './ChannelRef.js';
export type SearchItem = {
    channel: ChannelRef;
    message: ChannelMessageResponse;
    /**
     * How closely the line matches the words.
     */
    rank: number;
    /**
     * The words around the match, as plain text.
     */
    snippet?: (string | null);
};
