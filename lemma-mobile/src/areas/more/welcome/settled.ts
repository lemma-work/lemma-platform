import * as SecureStore from "expo-secure-store";
import { settledKey } from "@web/session/first-profile";

/** Whether this account's first profile step was continued or skipped, on
 *  this phone (the web keeps it per browser, `readSettled`/`writeSettled`).
 *  In the platform keystore, the way `session/store.ts` keeps everything
 *  else. A phone that will not remember asks once more, which is the whole
 *  cost. */

export async function readSettled(userId: string): Promise<boolean> {
    try {
        return (await SecureStore.getItemAsync(settledKey(userId))) === "1";
    } catch {
        return false;
    }
}

export async function writeSettled(userId: string): Promise<void> {
    try {
        await SecureStore.setItemAsync(settledKey(userId), "1");
    } catch {
        /* Kept for this launch only. */
    }
}
