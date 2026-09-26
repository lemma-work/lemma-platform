"use client";

import { openSettings } from "@/desktop/open-settings";
import { SetUpAiModelLink } from "@/desktop/set-up-on-this-mac";
import { useThisMacAvailability } from "@/desktop/this-mac-settings";
import { KeyIcon } from "@/ui/icons";

/** The one thing to do about a teammate with no model: add one.
 *
 *  Inside the Lemma app on the machine it runs on, that is Server setup —
 *  the installation's own model, which every teammate then falls back to.
 *  Everywhere else it is Settings → Models, where an organization adds a
 *  provider. Settings decides for itself whether this reader may change it. */
export function AddModelAction() {
    if (useThisMacAvailability() === "shown") return <SetUpAiModelLink compact={false} />;
    return <OpenModelsAction />;
}

/** "Open Settings → Models", beside a failure whose fix is there — a key the
 *  provider refused, a model it no longer serves. */
export function OpenModelsAction() {
    return (
        <button type="button" className="btn" onClick={() => openSettings("models")}>
            <KeyIcon size={13} /> Open Settings → Models
        </button>
    );
}
