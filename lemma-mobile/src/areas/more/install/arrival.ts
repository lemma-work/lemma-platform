import { heldStore } from "./model";
import { arrivedFromInstall, markInstallArrival } from "@web/site/import/pending-install";

/** The visit an install opens (the web's `markInstallArrival`).
 *
 *  Somebody who just installed a template came to use it, and the first
 *  profile step and the tour stand between them and what they installed.
 *  The web holds this in the tab's own storage; the phone holds it for as
 *  long as the app is open, so this visit stays quiet and the next launch
 *  offers both as usual. The tour and the first profile step read `held()`. */
const visit = heldStore({});

export const installArrival = {
    mark: (): void => markInstallArrival(visit),
    held: (): boolean => arrivedFromInstall(visit),
};
