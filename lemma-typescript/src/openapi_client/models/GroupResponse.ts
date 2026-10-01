/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GroupOwnerResponse } from './GroupOwnerResponse.js';
export type GroupResponse = {
    answers_outsiders: boolean;
    /**
     * The bot's own switch, over every group it is in. Off, nobody outside the pod is answered in any of them.
     */
    bot_answers_outsiders?: boolean;
    /**
     * The reader may switch outsiders for this group or take it over: they answer for it, nobody in the pod does, or they are an admin of the pod.
     */
    can_manage?: boolean;
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
    /**
     * Questions its people outside the pod passed on to you.
     */
    waiting_for_you?: number;
    /**
     * People outside the pod are answered here today: the group's switch is on, a member of the pod answers for them, and the bot's own switch is on.
     */
    welcomes_outsiders: boolean;
};
