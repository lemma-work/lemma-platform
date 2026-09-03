/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What a stopped import already wrote to the pod, and how to continue it.
 *
 * Apply is not transactional: each step commits in its own unit of work, so an
 * import that fails or is cancelled part-way leaves the pod changed and there
 * is no rollback. `committed_steps` says which steps landed, but a bare list
 * of integers does not tell anyone that the pod was modified at all, nor that
 * re-applying resumes instead of duplicating. This does.
 */
export type PartialApplyResponse = {
    /**
     * Whether applying this import again continues it. False once the job reached a status apply no longer accepts, in which case the pod keeps what was already applied and the rest must be imported afresh.
     */
    resumable: boolean;
    /**
     * Index of the first step still to run. Applying this import again resumes here; steps already applied are not repeated.
     */
    resume_from_step?: (number | null);
    /**
     * Plan steps already applied to this pod. Not undone.
     */
    steps_applied: number;
    /**
     * Steps in the approved plan.
     */
    steps_total: number;
};
