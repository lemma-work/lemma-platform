/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Send a proactive message to a pod member on this surface.
 */
export type SurfaceSendRequest = {
    /**
     * Message text to deliver.
     */
    message: string;
    /**
     * Target pod member (Lemma user id).
     */
    user_id: string;
};
