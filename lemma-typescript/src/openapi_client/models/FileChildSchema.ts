/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A derived child artifact of a processed document (converted markdown,
 * an extracted figure, or a renderable page). Fetch its bytes from
 * ``GET …/files/children/content?path=<child path>``.
 */
export type FileChildSchema = {
    content_type?: (string | null);
    kind: string;
    name: string;
    page_number?: (number | null);
    path: string;
    size_bytes?: (number | null);
};
