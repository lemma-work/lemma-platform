import { SAMPLE_LEMMAS } from "../../../data/sample";
import type { AboutSource } from "./source";

const DAY = 24 * 60 * 60_000;

/** Writes change the sample Lemmas themselves, in memory, so the Lemmas
 *  list and a Lemma's page agree with what was saved until the app closes. */
export function aboutSample(): AboutSource {
    const change = (podId: string, patch: Partial<(typeof SAMPLE_LEMMAS)[number]>) => {
        const at = SAMPLE_LEMMAS.findIndex((one) => one.id === podId);
        if (at >= 0) SAMPLE_LEMMAS[at] = { ...SAMPLE_LEMMAS[at], ...patch };
    };
    return {
        origin: async (podId) => ({
            createdAt: new Date(Date.now() - (podId.endsWith("1140") ? 41 : 12) * DAY).toISOString(),
            createdBy: podId.endsWith("1140") ? "user-sam" : "me",
        }),
        rename: async (podId, name) => change(podId, { name }),
        describe: async (podId, description) => change(podId, { description: description.trim() || null }),
        setIcon: async (podId, icon) => change(podId, { iconUrl: icon }),
        /* Nothing to upload to: the picture is shown from the phone itself. */
        uploadIcon: async (file) => file.uri,
        remove: async (podId) => {
            const at = SAMPLE_LEMMAS.findIndex((one) => one.id === podId);
            if (at >= 0) SAMPLE_LEMMAS.splice(at, 1);
        },
    };
}
