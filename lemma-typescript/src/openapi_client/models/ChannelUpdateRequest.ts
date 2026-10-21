/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactStatus } from './ContactStatus.js';
import type { Visibility } from './Visibility.js';
/**
 * Absent is unchanged; ``null`` clears where clearing means something.
 *
 * A group takes ``title``, ``topic``, ``visibility``, ``archived``,
 * ``answers_shared``. A contact's channel takes ``agents_answer``, ``hand_back_after_hours``,
 * ``status``, ``assignee`` (and needs ``contact.message``). Anything else
 * for the kind is 409.
 */
export type ChannelUpdateRequest = {
    /**
     * On also hands the contact back now: nobody is assigned.
     */
    agents_answer?: (boolean | null);
    /**
     * A group's: on, agents' answers made with the asker's access are read by everybody here; off, by the asker alone until they share one. Applies to answers posted from then on.
     */
    answers_shared?: (boolean | null);
    archived?: (boolean | null);
    /**
     * `me`, or a member's user id; `null` unassigns.
     */
    assignee?: (string | null);
    hand_back_after_hours?: (number | null);
    status?: (ContactStatus | null);
    title?: (string | null);
    topic?: (string | null);
    visibility?: (Visibility | null);
};
