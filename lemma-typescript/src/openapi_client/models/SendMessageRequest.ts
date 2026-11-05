/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SendMessageRequest = {
    /**
     * The sender's own id for this send, generated once per message the person sends and reused on every retry of it. A second send with an id already used in this conversation stores nothing and returns the first send's run, with deduplicated=true.
     */
    client_message_id?: (string | null);
    content: string;
    metadata?: (Record<string, any> | null);
};
