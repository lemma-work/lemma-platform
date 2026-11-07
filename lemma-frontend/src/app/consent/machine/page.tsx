import { MachineConsentHost } from "./consent-host";

/** Where an app sends somebody to ask whether it may use their machine.
 *
 *  Top-level rather than inside `/t`, like `sign-in-to-site`: it is one
 *  errand, usually in a popup the app opened, answered and closed. Mounting
 *  the workspace to show two buttons would cold-start everything behind them.
 *  The app id is read in the browser, from the query, so nothing here needs
 *  the request. */
export default function MachineConsentRoute() {
    return <MachineConsentHost />;
}
