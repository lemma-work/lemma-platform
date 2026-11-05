/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FilePart } from './FilePart.js';
import type { LinkPart } from './LinkPart.js';
import type { MentionPart } from './MentionPart.js';
export type ChannelPostRequest = {
    /**
     * The client's name for this line: sending it again is the same line. Shown back on the line to its writer, as sent.
     */
    client_message_id?: (string | null);
    /**
     * A note to the pod's members: never sent outside.
     */
    members_only?: boolean;
    /**
     * Mentions, files and links. Who a line names is read from here.
     */
    parts?: Array<(MentionPart | FilePart | LinkPart)>;
    reply_to_id?: (string | null);
    /**
     * Which outside place it goes to; by default the thread's, else the one the channel last heard from.
     */
    source_id?: (string | null);
    text?: string;
    /**
     * Reply in the thread of this line.
     */
    thread_root_id?: (string | null);
};
