/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Promote a conversation widget into a persisted app.
 *
 * The widget's stored source fragment (addressed by conversation + tool call) is
 * preserved, wrapped into a standalone document without embed-only chrome, and
 * deployed as the app's bundle.
 */
export type CreateAppFromWidgetRequest = {
    conversation_id: string;
    description?: (string | null);
    name: string;
    public_slug?: (string | null);
    tool_call_id: string;
    visibility?: (string | null);
};
