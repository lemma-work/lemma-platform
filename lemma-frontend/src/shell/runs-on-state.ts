import type { Choice, Runtime } from "@/data";

/** Whether the "runs on" picker has anything to run on.
 *
 *  - `none`: the organization has no model at all — nothing to pick, and the
 *    organization default is nothing either.
 *  - `default-missing`: models exist, but this teammate follows the
 *    organization default and that default is not one of them — on a server
 *    with no model of its own, the default names a provider that is not
 *    there, and the next message fails.
 *  - `null`: something real is chosen or inherited.
 *
 *  `live` is the non-retired list. `inherited` is `undefined` while it is
 *  still being read, so a slow answer is not mistaken for a missing one. */
export function modelSetupState(
    live: Runtime[],
    inherited: Choice | null | undefined,
    choice: Choice | null,
): "none" | "default-missing" | null {
    if (live.length === 0) return "none";
    /* No stated default is not evidence of a broken one. */
    if (choice || !inherited) return null;
    return live.some((runtime) => runtime.id === inherited.runtimeId) ? null : "default-missing";
}
