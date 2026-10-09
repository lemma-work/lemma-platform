/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PublicColumnResponse = {
    description: (string | null);
    /**
     * The form control to ask with.
     */
    input: string;
    name: string;
    options: Array<string>;
    /**
     * The table needs it, so it must be open.
     */
    required: boolean;
    type: string;
};
