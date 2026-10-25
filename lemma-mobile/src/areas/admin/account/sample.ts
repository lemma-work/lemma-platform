import type { UserResponse } from "lemma-sdk";
import { normalizeMobileNumber } from "@web/session/mobile-number";
import type { AccountSource, VerifyTransaction } from "./source";

/** Ada's profile, as sample data: no mobile number yet, so the way to add
 *  and verify one can be walked. Verifying is acted out: the code "arrives"
 *  a few seconds after it is asked for, and nothing is sent anywhere. The
 *  numbers are in ranges kept for fiction (UK 07700 900xxx, US 555-01xx). */

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

let ADA: UserResponse = {
    id: "me",
    email: "ada@acme.example",
    first_name: "Ada",
    last_name: "Lovelace",
    timezone: "Europe/London",
    country: "United Kingdom",
    mobile_number: null,
    mobile_verified_at: null,
    date_of_birth: null,
    is_active: true,
    is_superuser: false,
    is_verified: true,
    created_at: "2026-03-02T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
};

/** Somebody else in Acme already holds this one. */
const TAKEN = "+15550177";

let started: { at: number; transaction: VerifyTransaction; number: string | null } | null = null;
/** Verifications already answered, which stay answered. */
const verified = new Set<string>();

export function accountSample(): AccountSource {
    return {
        async profile() {
            await wait(150);
            return { ...ADA };
        },
        async saveProfile(patch) {
            await wait(500);
            const next = { ...ADA, ...patch, updated_at: new Date().toISOString() } as UserResponse;
            /* A different number is not the one that was proved. */
            if ("mobile_number" in patch && normalizeMobileNumber(patch.mobile_number ?? "") !== normalizeMobileNumber(ADA.mobile_number ?? "")) next.mobile_verified_at = null;
            ADA = next;
            return { ...ADA };
        },
        async whatsappVerification() {
            await wait(120);
            return { available: true, displayNumber: "+1 555-0100" };
        },
        async telegramVerification() {
            await wait(80);
            return false;
        },
        async startWhatsAppVerification(mobileNumber) {
            await wait(600);
            if (mobileNumber && normalizeMobileNumber(mobileNumber) === TAKEN) {
                throw Object.assign(new Error("This mobile number is already in use"), { statusCode: 409 });
            }
            const code = "Q7K2PX9M";
            const transaction: VerifyTransaction = {
                id: `sample-verification-${Date.now()}`,
                code,
                /* Not WhatsApp: a sample has nobody to send it to. */
                whatsappUrl: "https://example.com/whatsapp?text=" + encodeURIComponent("LEMMA VERIFY " + code),
                displayNumber: "+1 555-0100",
                expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
            };
            started = { at: Date.now(), transaction, number: mobileNumber ? normalizeMobileNumber(mobileNumber) : null };
            return transaction;
        },
        async verificationStatus(id) {
            await wait(200);
            if (verified.has(id)) return "VERIFIED";
            if (!started || started.transaction.id !== id) return "EXPIRED";
            if (Date.now() - started.at < 9000) return "PENDING";
            ADA = { ...ADA, mobile_number: started.number ?? "+447700900123", mobile_verified_at: new Date().toISOString() };
            started = null;
            verified.add(id);
            return "VERIFIED";
        },
    };
}
