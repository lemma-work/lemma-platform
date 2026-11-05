/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type HistoryMessage = {
    client_nonce?: (string | null);
    /**
     * A person on the team wrote it, not the bot.
     */
    from_person?: boolean;
    role: string;
    sequence: number;
    text: string;
};
