import * as SecureStore from "expo-secure-store";

/** Whether this person has had the tour on this phone: the web keeps it per
 *  browser, the phone per install. A store that will not answer offers it
 *  again, which is the whole cost. */
function keyOf(userId: string): string {
    return "lemma.tour-seen." + userId.replace(/[^A-Za-z0-9._-]/g, "_");
}

export async function readTourSeen(userId: string): Promise<boolean> {
    try {
        return (await SecureStore.getItemAsync(keyOf(userId))) === "1";
    } catch {
        return false;
    }
}

export async function writeTourSeen(userId: string): Promise<void> {
    try {
        await SecureStore.setItemAsync(keyOf(userId), "1");
    } catch {
        /* Asked again next time. */
    }
}
