import { linkedWhen, type BotChoice, type LinkedTelegramAccount } from "@web/session/telegram-bots";

/** Linking a Telegram account from the profile (the web's
 *  `session/link-telegram.tsx`), its decisions said for a phone: the web's
 *  words, with "space" said as "Lemma". Pure, so `tests/` can hold it. */

/** Whether the profile shows Telegram at all. Not until the accounts are
 *  read (not yet read is not none: somebody with several would be offered a
 *  first link); then when there is a bot to link through, an account to
 *  unlink, or a failure to say. */
export function telegramShown(held: { pending: boolean; unread: boolean; bots: number; linked: number }): boolean {
    if (held.pending) return false;
    return held.bots > 0 || held.linked > 0 || held.unread;
}

/** One linked account's row: its name, and when it was linked. */
export function linkedRow(account: LinkedTelegramAccount, now = new Date()): { label: string; value: string | null } {
    const when = linkedWhen(account.provedAt, now);
    return { label: account.name ?? "A Telegram account", value: when ? "linked " + when : null };
}

export const LINK_HINT = "Link your Telegram account so your Lemmas’ bots know it is you, in groups as well as in private chats.";

export function linkButton(linked: number): string {
    return linked > 0 ? "Link another account" : "Link Telegram";
}

/** The bot the link goes through, once there is one: the one Continue was
 *  pressed on, or the only one, with nothing to choose. `selected` is the
 *  one ticked meanwhile, the first until another is picked. */
export function botToOpen(choices: readonly BotChoice[], picked: string | null, going: BotChoice | null): { through: BotChoice | null; selected: BotChoice | null } {
    return {
        through: going ?? (choices.length === 1 ? choices[0] : null),
        selected: choices.find((choice) => choice.surfaceId === picked) ?? choices[0] ?? null,
    };
}
