/* The Telegram accounts a member proved from a Lemma session, and unlinking
   one. They are the member's on every bot, so they are read across all their
   spaces at once (`/surfaces/me/telegram-accounts`), not from any one. */

import { source } from "@/data";
import { lemma } from "./client";
import { readLinkedAccounts, type LinkedTelegramAccount } from "./telegram-bots";

/* The sample's: none until its link sheet confirms one, and kept while the tab
   is open. The sample's Telegram is always the same account, so linking it
   again replaces it rather than listing it twice. */
let sample: LinkedTelegramAccount[] = [];

export async function linkedTelegramAccounts(): Promise<LinkedTelegramAccount[]> {
    if (source.label === "sample") return [...sample];
    return readLinkedAccounts(await lemma().userSurfaces.telegramAccounts());
}

export async function unlinkTelegramAccount(id: string): Promise<void> {
    if (source.label === "sample") {
        sample = sample.filter((account) => account.id !== id);
        return;
    }
    await lemma().userSurfaces.unlinkTelegramAccount(id);
}

/** The sample has no server to keep a confirmed link, so its sheet says so here. */
export function rememberSampleLink(name: string | null): void {
    sample = [{ id: "sample-telegram-account", name, provedAt: new Date().toISOString() }];
}
