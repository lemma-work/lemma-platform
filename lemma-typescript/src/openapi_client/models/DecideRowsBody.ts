/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeciderDefinition } from './DeciderDefinition.js';
import type { JsonValue } from './JsonValue.js';
import type { Option } from './Option.js';
export type DecideRowsBody = {
    /**
     * A pod decider's name, or `system:<name>` for one that ships with Lemma. Leave out to ask the `definition` inline.
     */
    decider?: (string | null);
    /**
     * Questions to ask without a named decider. Nothing is learned for them.
     */
    definition?: (DeciderDefinition | null);
    /**
     * The field that identifies a row. Row numbers are used when absent.
     */
    id_field?: (string | null);
    /**
     * Per question, options to add to the declared ones for this call: the person's pods, the open conversations, a list built just now.
     */
    options?: Record<string, Record<string, Option>>;
    /**
     * Keep the decision. Off answers without keeping anything.
     */
    record?: boolean;
    rows: Array<JsonValue>;
    /**
     * Prefix for each row's subject (`<prefix>:<row id>`), so deciding the same rows again returns the recorded decisions.
     */
    subject_prefix?: (string | null);
    /**
     * Who may read the recorded decision, evidence included: only you, or everyone in the pod who can read deciders. Personal unless the state is the pod's to share, because evidence is whatever was decided about -- an inbox, a call, a conversation.
     */
    visibility?: DecideRowsBody.visibility;
};
export namespace DecideRowsBody {
    /**
     * Who may read the recorded decision, evidence included: only you, or everyone in the pod who can read deciders. Personal unless the state is the pod's to share, because evidence is whatever was decided about -- an inbox, a call, a conversation.
     */
    export enum visibility {
        PERSONAL = 'PERSONAL',
        POD = 'POD',
    }
}
