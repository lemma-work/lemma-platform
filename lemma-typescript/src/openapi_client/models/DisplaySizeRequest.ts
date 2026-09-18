/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The size the pane wants its picture to be, in CSS pixels.
 *
 * Bounded here because these numbers come from a browser window and decide
 * how much memory a framebuffer takes. The sandbox clamps again against the
 * framebuffer it actually allocated, which is the limit that cannot be
 * argued with.
 */
export type DisplaySizeRequest = {
    height: number;
    width: number;
};
