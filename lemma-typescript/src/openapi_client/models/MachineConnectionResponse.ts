/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type MachineConnectionResponse = {
    /**
     * What the machine reported it can do.
     */
    capabilities?: Record<string, any>;
    created_at?: (string | null);
    /**
     * What the machine called itself.
     */
    display_name: string;
    /**
     * Where Lemma's files live on the machine.
     */
    home?: (string | null);
    id: string;
    last_seen_at?: (string | null);
    /**
     * `host` (as your OS user) or `image` (Docker).
     */
    mode: string;
    /**
     * How refs name it: `mine:<name>`.
     */
    name: string;
    /**
     * Whether its link is up right now.
     */
    online: boolean;
    platform?: (string | null);
    /**
     * The pod it is shared into.
     */
    pod_id?: (string | null);
    runtime_version?: (string | null);
};
