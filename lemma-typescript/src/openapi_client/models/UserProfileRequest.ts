/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * User profile request schema.
 */
export type UserProfileRequest = {
    country?: (string | null);
    date_of_birth?: (string | null);
    first_name?: (string | null);
    last_name?: (string | null);
    mobile_number?: (string | null);
    /**
     * Deprecated: accepted and ignored, and will be removed. A Telegram account is linked by sharing a contact with a Lemma bot, never by a handle typed onto a profile.
     * @deprecated
     */
    telegram_username?: (string | null);
    timezone?: (string | null);
};
