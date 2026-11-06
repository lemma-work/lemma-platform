/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationRepositoryResponse } from './ConversationRepositoryResponse.js';
export type ConversationRepositoryListResponse = {
    /**
     * False when the installation covers more repositories than are listed. Create still accepts the ones not shown.
     */
    complete?: boolean;
    items: Array<ConversationRepositoryResponse>;
};
