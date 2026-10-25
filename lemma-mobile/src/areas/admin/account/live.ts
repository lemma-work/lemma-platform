import type { LiveContext } from "../../context";
import type { AccountSource, VerifyStatus } from "./source";

/** The same calls the web makes: `users.current` and `users.upsertProfile`
 *  for the profile, and the WhatsApp verification routes, which are kept
 *  out of the API's schema and so are reached as plain requests
 *  (`session/use-mobile-verification.ts`). */
export function accountLive(context: LiveContext): AccountSource {
    const client = () => context.client();
    return {
        async profile() {
            return client().users.current();
        },
        async saveProfile(patch) {
            return client().users.upsertProfile(patch);
        },
        async whatsappVerification() {
            try {
                const said = await client().request<{ available?: boolean; display_number?: string | null }>("GET", "/auth/mobile-verification/whatsapp/config");
                return { available: said.available === true, displayNumber: said.display_number ?? null };
            } catch {
                /* Unreachable and switched off are the same to a button that
                   would otherwise fail on the first press. */
                return { available: false, displayNumber: null };
            }
        },
        async telegramVerification() {
            try {
                const said = await client().request<{ enabled?: boolean }>("GET", "/auth/telegram/config");
                return said.enabled === true;
            } catch {
                return false;
            }
        },
        async startWhatsAppVerification(mobileNumber) {
            const made = await client().request<{ transaction_id: string; code: string; whatsapp_url: string; display_number: string; expires_at: string }>(
                "POST",
                "/auth/mobile-verification/whatsapp/start",
                { body: mobileNumber ? { mobile_number: mobileNumber } : {} },
            );
            return { id: made.transaction_id, code: made.code, whatsappUrl: made.whatsapp_url, displayNumber: made.display_number, expiresAt: made.expires_at };
        },
        async verificationStatus(id) {
            const answer = await client().request<{ status?: string }>("GET", "/auth/mobile-verification/whatsapp/status/" + encodeURIComponent(id));
            const status = String(answer.status ?? "PENDING").toUpperCase();
            return (status === "VERIFIED" || status === "EXPIRED" ? status : "PENDING") as VerifyStatus;
        },
    };
}
