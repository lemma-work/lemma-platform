/** Where the sample chats' files live, kept apart from the files
 *  themselves (`sample-media.ts`) so the transcripts read without them. */

const DAY = "/me/c/2026-10-24/refund-for-a-broken-order";

/** The photos the person attached to the refund chat. */
export const SAMPLE_PHOTOS = [
    { name: "lamp-cracked.jpg", path: `${DAY}/lamp-cracked.jpg` },
    { name: "lamp-box.jpg", path: `${DAY}/lamp-box.jpg` },
    { name: "lamp-shade.jpg", path: `${DAY}/lamp-shade.jpg` },
    { name: "lamp-label.jpg", path: `${DAY}/lamp-label.jpg` },
    { name: "lamp-whole.jpg", path: `${DAY}/lamp-whole.jpg` },
] as const;

export const SAMPLE_ORDER = "/inbox/northwind/Order 4471.pdf";
export const SAMPLE_SHEET = "/inbox/northwind/order-4471.csv";
export const SAMPLE_VOICEMAIL = "/inbox/northwind/Voicemail from Maria.m4a";

export const SAMPLE_PAGES: Record<string, number> = { [SAMPLE_ORDER]: 2 };

export const SAMPLE_SIZES: Record<string, number> = {
    [SAMPLE_ORDER]: 84_211,
    [SAMPLE_VOICEMAIL]: 29_888,
};
