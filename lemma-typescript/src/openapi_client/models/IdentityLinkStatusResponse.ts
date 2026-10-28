/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LinkState } from './LinkState.js';
import type { TelegramAccountResponse } from './TelegramAccountResponse.js';
export type IdentityLinkStatusResponse = {
    account?: (TelegramAccountResponse | null);
    state: LinkState;
};
