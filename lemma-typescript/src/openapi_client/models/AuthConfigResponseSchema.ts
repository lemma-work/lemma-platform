/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AuthConfigResponseSchema = {
    /**
     * How this install authenticates, which is not always what the connector's catalog entry says. `mcp` is one catalog entry standing for every server a tenant may point at: the entry says API_KEY, but an install whose server described its own authorization when it was created signs in through a browser and answers OAUTH2 here. Branch on this rather than on the connector's kind when deciding how to connect an install.
     */
    auth_scheme?: (string | null);
    config?: (Record<string, any> | null);
    config_source: string;
    connector_id: string;
    created_at: string;
    id: string;
    is_default?: boolean;
    kind: string;
    metadata?: (Record<string, any> | null);
    name: string;
    organization_id: string;
    status: string;
    updated_at: string;
};
