/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A one-time link that connects whoever opens it in Telegram to this user.
 *
 * Single use and short-lived; open it straight away rather than sharing it.
 */
export type TelegramLinkResponse = {
    bot_username: string;
    expires_at: string;
    pod_id?: (string | null);
    url: string;
};
