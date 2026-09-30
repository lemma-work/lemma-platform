/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type GroupUpdateRequest = {
    /**
     * Answer people outside the pod in this group. Switching it on where nobody answers for them makes the caller the one who does.
     */
    answers_outsiders?: (boolean | null);
    /**
     * The caller answers for this group's outsiders from now on.
     */
    take_over?: boolean;
};
