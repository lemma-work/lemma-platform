import type { UserProfileRequest, UserResponse } from "lemma-sdk";

/** Whether this server can verify a number over WhatsApp at all: it is off
 *  unless a deployment has set it up, so "no" is the ordinary answer. */
export interface VerifyConfig {
    available: boolean;
    /** Lemma's verification number, when the server says it up front. */
    displayNumber: string | null;
}

/** One verification in flight: a code, sent from the phone being claimed,
 *  to Lemma's verification number, before it runs out. */
export interface VerifyTransaction {
    id: string;
    code: string;
    /** `https://wa.me/<Lemma's number>?text=LEMMA%20VERIFY%20<code>`: the
     *  message written already, which is the point. */
    whatsappUrl: string;
    displayNumber: string;
    expiresAt: string;
}

export type VerifyStatus = "PENDING" | "VERIFIED" | "EXPIRED";

/** Your profile: name, time zone, mobile number and its verification (PARITY.md, F).
 *
 *  The web's `session/profile-panel.tsx` and `use-mobile-verification.ts`:
 *  the same profile calls, and the verification routes the SDK has no
 *  methods for. The live side is `live.ts`, the sample side `sample.ts`;
 *  screens reach it through `hooks.ts`, never the SDK directly. */
export interface AccountSource {
    /** The signed-in person, as the server holds them. */
    profile(): Promise<UserResponse>;
    /** Save only what changed; answers the person as saved. */
    saveProfile(patch: UserProfileRequest): Promise<UserResponse>;
    whatsappVerification(): Promise<VerifyConfig>;
    /** Whether this server proves numbers through Telegram instead. That
     *  hand-off rides on a browser's cookie session, which the phone never
     *  holds, so the phone can only say where it is done. */
    telegramVerification(): Promise<boolean>;
    /** Start a WhatsApp verification. With a number, the server checks
     *  nobody else holds it first; without, whichever phone sends the code
     *  becomes the number. */
    startWhatsAppVerification(mobileNumber?: string): Promise<VerifyTransaction>;
    verificationStatus(id: string): Promise<VerifyStatus>;
}
