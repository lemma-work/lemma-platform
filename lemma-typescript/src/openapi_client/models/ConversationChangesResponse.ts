/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationChangedFile } from './ConversationChangedFile.js';
import type { ConversationChangesTotals } from './ConversationChangesTotals.js';
/**
 * What the conversation's checkout holds that its start point does not.
 */
export type ConversationChangesResponse = {
    /**
     * Commits on the checkout since `base_sha`.
     */
    ahead?: number;
    /**
     * The start point compared against: `origin/<ref>`, the ref itself for a tag or a commit, or the branch `origin/HEAD` names (as `origin/main`). Null when it could not be found in the checkout; the files are then its uncommitted work only.
     */
    base_ref?: (string | null);
    /**
     * The merge base of the checkout's HEAD with `base_ref`.
     */
    base_sha?: (string | null);
    /**
     * The checkout's branch; null when detached.
     */
    branch?: (string | null);
    /**
     * Every file that differs from `base_sha` in the working tree -- committed, staged or not -- then the untracked files.
     */
    files?: Array<ConversationChangedFile>;
    head_sha?: (string | null);
    /**
     * The unified diff, when asked for with `patch=true`.
     */
    patch?: (string | null);
    /**
     * `ready`: read now, and everything below is filled in. `no_repository`: the conversation was not opened in a repository. `not_checked_out`: it was, and its machine answered, but the conversation's directory holds no checkout -- cleared, or never made on this machine. `machine_unavailable`: the machine it works on is not running -- it is not started to answer, so a conversation with no run yet answers this -- or a run of the conversation may not use it now. `elsewhere`: its last run worked on its person's own Mac (a coding agent through Agent Host, or host execution), where the checkout is.
     */
    state: ConversationChangesResponse.state;
    totals?: ConversationChangesTotals;
    /**
     * Something was cut to fit: the patch at `max_patch_bytes`, or the file list in a very large change.
     */
    truncated?: boolean;
    /**
     * Edits not yet committed, or untracked files, are present.
     */
    uncommitted?: boolean;
};
export namespace ConversationChangesResponse {
    /**
     * `ready`: read now, and everything below is filled in. `no_repository`: the conversation was not opened in a repository. `not_checked_out`: it was, and its machine answered, but the conversation's directory holds no checkout -- cleared, or never made on this machine. `machine_unavailable`: the machine it works on is not running -- it is not started to answer, so a conversation with no run yet answers this -- or a run of the conversation may not use it now. `elsewhere`: its last run worked on its person's own Mac (a coding agent through Agent Host, or host execution), where the checkout is.
     */
    export enum state {
        READY = 'ready',
        NO_REPOSITORY = 'no_repository',
        NOT_CHECKED_OUT = 'not_checked_out',
        MACHINE_UNAVAILABLE = 'machine_unavailable',
        ELSEWHERE = 'elsewhere',
    }
}
