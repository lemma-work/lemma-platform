/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeciderDefinition } from './DeciderDefinition.js';
import type { Option } from './Option.js';
import type { SampleRowBody } from './SampleRowBody.js';
export type DeciderTestBody = {
    /**
     * A pod decider's name, or `system:<name>` for one that ships with Lemma. Leave out to ask the `definition` inline.
     */
    decider?: (string | null);
    /**
     * Questions to ask without a named decider. Nothing is learned for them.
     */
    definition?: (DeciderDefinition | null);
    /**
     * Per question, options to add to the declared ones for this call: the person's pods, the open conversations, a list built just now.
     */
    options?: Record<string, Record<string, Option>>;
    /**
     * Keep the decision. Off answers without keeping anything.
     */
    record?: boolean;
    rows: Array<SampleRowBody>;
    /**
     * Who may read the recorded decision, evidence included: only you, or everyone in the pod who can read deciders. Personal unless the state is the pod's to share, because evidence is whatever was decided about -- an inbox, a call, a conversation.
     */
    visibility?: DeciderTestBody.visibility;
};
export namespace DeciderTestBody {
    /**
     * Who may read the recorded decision, evidence included: only you, or everyone in the pod who can read deciders. Personal unless the state is the pod's to share, because evidence is whatever was decided about -- an inbox, a call, a conversation.
     */
    export enum visibility {
        PERSONAL = 'PERSONAL',
        POD = 'POD',
    }
}
