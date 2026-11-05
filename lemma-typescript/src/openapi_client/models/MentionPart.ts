/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Somebody the line names, resolved: a member, a contact or an agent.
 */
export type MentionPart = {
    id: string;
    kind: MentionPart.kind;
    name?: (string | null);
    type: string;
};
export namespace MentionPart {
    export enum kind {
        MEMBER = 'MEMBER',
        CONTACT = 'CONTACT',
        AGENT = 'AGENT',
    }
}
