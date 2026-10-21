/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SenderKind } from './SenderKind.js';
/**
 * Somebody in a channel, said one way everywhere: a member of the pod, one
 * of its contacts, or one of its agents. There is nobody else -- everybody
 * outside the pod is a contact.
 */
export type PersonRef = {
    /**
     * The member's user id, the contact's id or the agent's id; absent for a member or an agent since removed.
     */
    id?: (string | null);
    kind: SenderKind;
    /**
     * What to call them, where known.
     */
    name?: (string | null);
};
