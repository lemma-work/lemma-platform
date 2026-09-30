/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GroupOwnerResponse } from './GroupOwnerResponse.js';
import type { GroupPersonResponse } from './GroupPersonResponse.js';
import type { GroupWaitingResponse } from './GroupWaitingResponse.js';
export type GroupDetailResponse = {
    answers_outsiders: boolean;
    external_channel_id?: (string | null);
    id: string;
    invite_link?: (string | null);
    last_message_at?: (string | null);
    /**
     * Who answers for the people outside the pod.
     */
    owner?: (GroupOwnerResponse | null);
    /**
     * Asked of the platform and not yet confirmed (WhatsApp).
     */
    pending?: boolean;
    people: Array<GroupPersonResponse>;
    /**
     * People in the pod seen speaking here; none where not kept.
     */
    people_in_pod?: (number | null);
    /**
     * People outside the pod seen speaking here.
     */
    people_outside?: (number | null);
    platform: string;
    /**
     * A Slack channel shared with another company.
     */
    shared_externally?: boolean;
    surface_name: string;
    title?: (string | null);
    updated_at: string;
    waiting: Array<GroupWaitingResponse>;
    /**
     * Questions its people outside the pod passed on to you.
     */
    waiting_for_you?: number;
    /**
     * Switched on, and somebody answers for them.
     */
    welcomes_outsiders: boolean;
};
