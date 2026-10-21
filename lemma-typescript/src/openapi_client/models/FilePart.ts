/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A file of the pod's the line shares.
 *
 * Whoever writes the line must be able to read the file; it is then read
 * through the line (``GET …/messages/{m}/files/{file_id}``), by whoever may
 * see the line, as its writer reads it -- never through the file's own
 * folder, which a private channel's readers need not share.
 */
export type FilePart = {
    file_id?: (string | null);
    mime_type?: (string | null);
    name: string;
    path: string;
    size?: (number | null);
    type: string;
};
