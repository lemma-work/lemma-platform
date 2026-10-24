import { MACHINE_CONSENT_MESSAGE_TYPE } from "lemma-sdk";

/** The rules of the machine consent page, apart from the page: which app a
 *  link is for, and how the window that opened it hears the answer. The
 *  hooks are in `consent.ts`. */

export interface MachineConsent {
    app_id: string;
    pod_id: string;
    /** Null when the app has since been deleted. */
    app_name?: string | null;
    granted_at: string;
}

const APP_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The app a consent link is for, or null for a link that names none.
 *
 *  Only an id is read, never a name: the link is written by the app asking,
 *  and a name in it would be whatever the app wanted to be called. */
export function appIdFromSearch(search: string): string | null {
    const found = new URLSearchParams(search).get("app")?.trim() ?? "";
    return APP_ID.test(found) ? found.toLowerCase() : null;
}

/** What the window that opened this page is told, once there is an answer. */
export function consentMessage(appId: string, granted: boolean) {
    return { type: MACHINE_CONSENT_MESSAGE_TYPE, app_id: appId, granted };
}

/** Tell the opener and close, where both are possible.
 *
 *  Returns whether the tab is going away. A tab somebody opened themselves
 *  cannot be closed by script, so that case shows "you can close this tab"
 *  rather than a button that does nothing. The message carries an id and a
 *  yes or no, nothing else, so it goes to whichever origin opened this. */
export function finish(appId: string, granted: boolean, view: Pick<Window, "opener" | "close"> = window): boolean {
    const opener = view.opener as Window | null;
    if (!opener) return false;
    try {
        opener.postMessage(consentMessage(appId, granted), "*");
    } catch {
        return false;
    }
    view.close();
    return true;
}
