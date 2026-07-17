/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type VariableSpecResponse = {
    /**
     * For a connector account variable, the connector the account must belong to (e.g. 'slack'), so the importer can connect the right connector. Null for non-connector variables.
     */
    connector?: (string | null);
    default?: (string | null);
    description?: (string | null);
    kind: string;
    name: string;
    /**
     * For a connector account variable, the auth provider backing the connector ('LEMMA' or 'COMPOSIO'), so the importer connects/selects an account through the right provider. Null for non-connector variables.
     */
    provider?: (string | null);
    required?: boolean;
};

