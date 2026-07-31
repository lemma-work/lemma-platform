/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Runtime framework used to execute an agent.
 *
 * Two kinds, not one per coding tool: ``LEMMA`` runs in-process, ``HARNESS``
 * dispatches through Agent Host. Which tool Agent Host runs is identified by
 * ``harness_id`` on the runtime profile, so the retired per-tool values
 * (``CODEX``, ``CLAUDE_CODE``, ``OPENCODE``, ``CURSOR``, ``ANTIGRAVITY``) went
 * away with the local daemon that needed them.
 *
 * No stored row is read back through this enum — a persisted runtime profile
 * names a ``RuntimeProfileProtocol``, and the kind is derived from that — so
 * dropping those values cannot fail a history read. Back-compat for retired
 * *protocols* is handled where it belongs, in the profile repository.
 */
export enum HarnessKind {
    LEMMA = 'LEMMA',
    HARNESS = 'HARNESS',
}
