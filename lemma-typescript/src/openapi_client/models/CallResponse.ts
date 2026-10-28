/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CallResponse = {
    /**
     * The call, which is also its conversation.
     */
    call_id: string;
    /**
     * The voice engines this server can open, the default first.
     */
    engines: Array<'gemini_live' | 'openai_realtime' | 'cascade'>;
};
