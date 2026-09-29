/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TelegramLinkPod } from './TelegramLinkPod.js';
/**
 * What a link to the shared Telegram bot would connect, before minting one.
 */
export type TelegramLinkOptionsResponse = {
    bot_username: string;
    pod_id?: (string | null);
    pods: Array<TelegramLinkPod>;
};
