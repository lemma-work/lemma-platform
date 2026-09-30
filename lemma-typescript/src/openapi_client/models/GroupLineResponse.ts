/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type GroupLineResponse = {
    /**
     * On the bot's lines: answered from what the pod made Public.
     */
    answered_from_public?: boolean;
    /**
     * On the bot's lines: whom it answered.
     */
    answered_name?: (string | null);
    at: string;
    author_external_id?: (string | null);
    author_name?: (string | null);
    from_bot: boolean;
    in_pod: boolean;
    text: string;
};
