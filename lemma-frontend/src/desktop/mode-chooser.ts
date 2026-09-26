import { invoke, isDesktop } from "./bridge";

/** Back to the app's Cloud-or-Local chooser, which the hosted sign-in
 *  screen came from.
 *
 *  The hosted site cannot switch the app by itself; the shell does it, and
 *  only for this page (`mode_chooser_return_allowed`). False when there is no
 *  shell to ask or it refused -- an older app, say -- and the caller shows its
 *  own "cancelled" page instead. */
export async function returnToModeChooser(call: typeof invoke = invoke, inApp: () => boolean = isDesktop): Promise<boolean> {
    if (!inApp()) return false;
    try {
        await call("return_to_mode_chooser");
        return true;
    } catch {
        return false;
    }
}
