import type { UserResponse } from "lemma-sdk";
import { SAMPLE_LEMMAS } from "../../../data/sample";
import { normalizeMobileNumber } from "@web/session/mobile-number";
import { botChoices, type LinkedTelegramAccount, type TelegramBot } from "@web/session/telegram-bots";
import type { AccountSource, VerifyTransaction } from "./source";

/** Ada's profile, as sample data: no mobile number yet, so the way to add
 *  and verify one can be walked. Verifying is acted out: the code "arrives"
 *  a few seconds after it is asked for, and nothing is sent anywhere. The
 *  numbers are in ranges kept for fiction (UK 07700 900xxx, US 555-01xx).
 *  Her Telegram: three bots to pick from, one old account to unlink, and her
 *  current one linked when the chats sample's link sheet confirms it. */

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

const [SUPPORT, LAUNCH, RENEWALS] = SAMPLE_LEMMAS;

/** Ada's Telegram bots: Support and Renewals answer on Lemma's shared bot,
 *  and Launch has a bot of its own (the chats sample's `@launch_acme_bot`),
 *  so there is a bot to pick. */
const BOTS: TelegramBot[] = [
    { surfaceId: "surface-telegram-shared-support", podId: SUPPORT.id, podName: SUPPORT.name, handle: "@lemma_sample_bot", shared: true },
    { surfaceId: "surface-telegram-shared-renewals", podId: RENEWALS.id, podName: RENEWALS.name, handle: "@lemma_sample_bot", shared: true },
    { surfaceId: "surface-telegram", podId: LAUNCH.id, podName: LAUNCH.name, handle: "@launch_acme_bot", shared: false },
];

/** The Telegram accounts Ada proved: an old phone's, to unlink, until the
 *  chats sample's link sheet confirms her current one
 *  (`rememberSampleLink`). Kept while the app is open. */
let linked: LinkedTelegramAccount[] = [
    { id: "sample-telegram-old", name: "Ada (old phone)", provedAt: new Date(Date.now() - 40 * 86_400_000).toISOString() },
];

/** A link confirmed in sample data, from a group or from the profile: the
 *  sample's Telegram is always the same account, so linking it again
 *  replaces it rather than listing it twice (the web's `rememberSampleLink`). */
export function rememberSampleLink(account: { id: string; name: string | null }): void {
    linked = [...linked.filter((one) => one.id !== account.id), { id: account.id, name: account.name, provedAt: new Date().toISOString() }];
}

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
        async deleteAccount() {
            /* Sample data has nobody to delete: refused the way the API
               refuses, so the screen says it in its own place. */
            await wait(200);
            throw { statusCode: 400, code: "SAMPLE_DATA", message: "Sample data has no account to delete. Sign in to delete yours." };
        },
        async myTelegramSurfaces() {
            await wait(120);
            return { surfaceIds: BOTS.map((bot) => bot.surfaceId), podIds: [...new Set(BOTS.map((bot) => bot.podId))], preferred: null };
        },
        async telegramBots(mine) {
            await wait(300);
            return botChoices(BOTS.filter((bot) => mine.surfaceIds.includes(bot.surfaceId)), mine.preferred);
        },
        async telegramAccounts() {
            await wait(150);
            return linked.map((one) => ({ ...one }));
        },
        async unlinkTelegramAccount(id) {
            await wait(300);
            if (!linked.some((one) => one.id === id)) throw Object.assign(new Error("That Telegram account is not linked to you."), { statusCode: 404 });
            linked = linked.filter((one) => one.id !== id);
        },
    };
}
