/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How a profile reaches its runtime.
 *
 * The retired local daemon needed one protocol per coding tool
 * (``CODEX_APP_SERVER``, ``CLAUDE_CODE``, ``OPENCODE``, ``CURSOR``,
 * ``ANTIGRAVITY``). Agent Host needs one: the tool is identified by the
 * profile's ``harness_id``. Stored rows can still carry a retired value, so
 * the profile repository skips protocols this enum no longer knows rather
 * than failing the whole listing.
 */
export enum RuntimeProfileProtocol {
    OPENAI_COMPATIBLE = 'OPENAI_COMPATIBLE',
    ANTHROPIC_COMPATIBLE = 'ANTHROPIC_COMPATIBLE',
    AZURE_OPENAI = 'AZURE_OPENAI',
    GOOGLE_VERTEX = 'GOOGLE_VERTEX',
    AGENT_HOST = 'AGENT_HOST',
}
