/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
export type SourceSettingsResponse = {
    /**
     * This group's own switch for people outside the pod.
     */
    answers_outsiders: boolean;
    /**
     * The bot's own switch, over every group it is in. Off, nobody outside the pod is answered in any of them.
     */
    bot_answers_outsiders?: boolean;
    /**
     * The reader may switch outsiders here or take it over: they answer for it, nobody in the pod does, or they are an admin of the pod.
     */
    can_manage?: boolean;
    channel_id: string;
    /**
     * The source's id, as the channel lists it.
     */
    id: string;
    invite_link?: (string | null);
    /**
     * The member who answers for its outsiders.
     */
    owner?: (PersonRef | null);
    /**
     * Asked of the platform and not yet confirmed (WhatsApp).
     */
    pending?: boolean;
    platform: string;
    /**
     * A Slack channel shared with another company.
     */
    shared_externally?: boolean;
    surface_name: string;
    title?: (string | null);
    updated_at: string;
    /**
     * People outside the pod are answered here today: the group's switch is on, a member of the pod answers for them, and the bot's own switch is on.
     */
    welcomes_outsiders: boolean;
};
