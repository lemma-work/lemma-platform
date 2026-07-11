/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Configuration for Loop node.
 */
export type LoopNodeConfig = {
    /**
     * Id of the first node of the loop body executed per item.
     */
    child_node_id: string;
    /**
     * Alias for the current item inside the loop body, available as `loop.<item_var_name>` (the item is always available as `loop.item`).
     */
    item_var_name?: string;
    /**
     * JMESPath to an array in the run context to iterate over.
     */
    items_path: string;
};
