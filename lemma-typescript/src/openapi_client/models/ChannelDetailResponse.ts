/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelKind } from './ChannelKind.js';
import type { ChannelParticipantResponse } from './ChannelParticipantResponse.js';
import type { ChannelSourceResponse } from './ChannelSourceResponse.js';
export type ChannelDetailResponse = {
    /**
     * Which contact's channel this is.
     */
    contact_id?: (string | null);
    created_at: string;
    /**
     * A member answering by hand; agents do not answer meanwhile.
     */
    handler_user_id?: (string | null);
    id: string;
    kind: ChannelKind;
    last_message_at?: (string | null);
    /**
     * Whose direct channel this is.
     */
    member_user_id?: (string | null);
    participants?: Array<ChannelParticipantResponse>;
    sources?: Array<ChannelSourceResponse>;
    title?: (string | null);
};
