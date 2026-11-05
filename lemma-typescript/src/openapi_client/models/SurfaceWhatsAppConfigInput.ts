/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * WhatsApp settings. Mirrored across request and response.
 */
export type SurfaceWhatsAppConfigInput = {
    /**
     * The template's language code.
     */
    language?: string;
    /**
     * An approved message template on this number's business account. Sent to a member whose 24-hour window has closed, holding the message itself until they reply. Unset, such a message is refused.
     */
    reengage_template?: (string | null);
};
