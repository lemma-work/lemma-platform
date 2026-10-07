/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AskablePodResponse = {
    /**
     * It linked itself to this pod, so it can be asked with nobody present.
     */
    connected: boolean;
    /**
     * What the pod is for.
     */
    description?: (string | null);
    icon_url?: (string | null);
    name: string;
    pod_id: string;
    /**
     * The caller is in it too, so it can be asked as them.
     */
    through_you: boolean;
};
