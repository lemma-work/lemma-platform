/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type VariableSpecResponse = {
    /**
     * For a connector account variable, the connector the account must belong to (e.g. 'slack'), so the importer can connect the right connector. Null for non-connector variables.
     */
    connector?: (string | null);
    /**
     * For a connector account variable, which of the connector's kinds the source install used ('composio', 'package', 'mcp', 'sql', 'http'), so the importer selects an account of the same kind. Null for non-connector variables.
     */
    connector_kind?: (string | null);
    default?: (string | null);
    description?: (string | null);
    kind: string;
    name: string;
    required?: boolean;
};
