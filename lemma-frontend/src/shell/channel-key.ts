/** One key per channel a person recognises: every mail provider is "EMAIL".
 *  Kept apart from `channels.tsx` so code without JSX can use it. */
export function channelKey(platform: string) {
    const key = platform.toUpperCase();
    return ["EMAIL", "RESEND", "GMAIL", "OUTLOOK"].includes(key) ? "EMAIL" : key;
}
