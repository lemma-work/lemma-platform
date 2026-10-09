/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactAnswer } from './ContactAnswer.js';
/**
 * Whom the bot answers in private chats beyond the pod's members. Mirrored.
 */
export type SurfaceContactsConfig = {
    /**
     * `off`: members only. `known`: members and the pod's existing contacts. `anyone`: a stranger becomes a contact with their first message. Only a bot that is the pod's own (its own token, number or email address) answers contacts.
     */
    answer?: ContactAnswer;
    /**
     * The member contacts' conversations belong to. Defaults to whoever turns contacts on; must be a member of the pod.
     */
    looked_after_by?: (string | null);
};
