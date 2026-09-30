/** Chatting with your agents through this Lemma's own Telegram bot.
 *
 *  The token saved in Server setup is the server's shared bot. Nothing new on
 *  the server makes it yours: the bot already asks a new chat to Share my
 *  contact, and a contact you share yourself proves the phone. Desktop lets
 *  that match the number on your profile even though nothing here could verify
 *  it (`SURFACE_ALLOW_UNVERIFIED_PHONE_MATCH`), so the whole setup is a number
 *  on the profile and a chat opened with the bot. Pure, so the tests can read
 *  it without a webview. */

/** The bot's @name, from the Telegram Test's "Connected as @name." — the
 *  locald probe asks `getMe` and says it that way. Null when the answer names
 *  no bot (the probe falls back to "your bot"). */
export function botFromTestDetail(detail: unknown): string | null {
    const match = /^Connected as @([A-Za-z0-9_]{3,64})\.?$/.exec(String(detail ?? "").trim());
    return match ? match[1] : null;
}

/** The chat with the bot. Built from a validated name, so it is never
 *  anything but a t.me link. */
export function telegramBotUrl(bot: string): string {
    if (!/^[A-Za-z0-9_]{3,64}$/.test(bot)) throw new Error("Not a Telegram bot name");
    return "https://t.me/" + bot;
}

export type TelegramBotCard =
    | { kind: "hidden" }
    | { kind: "loading" }
    | { kind: "problem"; text: string }
    | { kind: "ready"; bot: string; title: string; url: string };

/** What shows under the Telegram token: nothing until a token is saved (and
 *  its edit saved too), then the bot by name once Telegram has answered. */
export function telegramBotCard(input: {
    saved: boolean;
    unsaved: boolean;
    detail?: unknown;
    pending?: boolean;
    failed?: boolean;
}): TelegramBotCard {
    if (!input.saved || input.unsaved) return { kind: "hidden" };
    if (input.pending) return { kind: "loading" };
    const bot = input.failed ? null : botFromTestDetail(input.detail);
    if (!bot) return { kind: "problem", text: "Telegram didn’t answer with the bot’s name. Press Test above to check the token." };
    return { kind: "ready", bot, title: `@${bot} is ready`, url: telegramBotUrl(bot) };
}
