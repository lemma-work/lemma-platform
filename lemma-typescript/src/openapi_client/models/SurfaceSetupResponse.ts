/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSurfaceStatus } from './AgentSurfaceStatus.js';
import type { SurfaceAdminConsentInfo } from './SurfaceAdminConsentInfo.js';
import type { SurfacePlatform } from './SurfacePlatform.js';
import type { SurfacePlatformSetupGuide } from './SurfacePlatformSetupGuide.js';
import type { SurfaceSetupAction } from './SurfaceSetupAction.js';
/**
 * Everything a caller needs to finish setting up a surface, in one read.
 *
 * Merges the former setup-status, admin-consent, and platform-checklist
 * endpoints. Works both before a surface exists (`exists=False`, guide only)
 * and after.
 *
 * ``ready`` is True when the user has nothing left to do (system credentials,
 * or an already-granted consent). ``actions`` is populated *only* when the
 * user must act — e.g. point their own Slack/Teams/WhatsApp app at Lemma —
 * so the UI can show a clean "Ready" state otherwise.
 */
export type SurfaceSetupResponse = {
    actions?: Array<SurfaceSetupAction>;
    admin_consent?: (SurfaceAdminConsentInfo | null);
    exists: boolean;
    guide: SurfacePlatformSetupGuide;
    platform: SurfacePlatform;
    ready?: boolean;
    status: AgentSurfaceStatus;
    webhook_url?: (string | null);
};
