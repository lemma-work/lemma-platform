/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The only part of the state any engine sees.
 *
 * `fields` are top-level keys or dotted paths into an object state. Leaving
 * them out passes the whole state. Either way the rendered view is cut at
 * `max_chars`, and the engines are told it was.
 */
export type InputView = {
    fields?: (Array<string> | null);
    max_chars?: number;
};
