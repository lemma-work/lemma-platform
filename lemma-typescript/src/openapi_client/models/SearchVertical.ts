/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What kind of result the caller wants.
 *
 * Not every provider serves every vertical, so `BaseSearchClient` advertises
 * what it supports and the caller degrades honestly rather than silently
 * returning web pages for a video query.
 */
export enum SearchVertical {
    WEB = 'web',
    NEWS = 'news',
    IMAGES = 'images',
    VIDEOS = 'videos',
}
