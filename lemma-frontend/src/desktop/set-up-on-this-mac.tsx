"use client";

import { useQuery } from "@tanstack/react-query";
import { KeyIcon } from "@/ui/icons";
import { openSettings } from "./open-settings";
import { useThisComputer } from "./this-computer";
import { formConfigured, thisMac, type CredentialForm } from "./this-mac";
import { useThisMacAvailability } from "./this-mac-settings";

/** "Set up on this Mac", where a connector or channel needs it.
 *
 *  On a local install the OAuth apps and bots behind Gmail, GitHub, Slack and
 *  the rest are this computer's to configure, and until they are, connecting
 *  one has no honest answer — "ask your administrator" when the administrator
 *  is the person reading. So the screen that needed the credential offers the
 *  form, opened at the right place, instead of a dead end.
 *
 *  Only in the app's own window on this installation's loopback origin, and only
 *  while that form is actually empty: a link to set up something already set
 *  up sends people to check work that was done. */
export function SetUpOnThisMac({ form, compact = false }: { form: CredentialForm | null; compact?: boolean }) {
    const noun = useThisComputer();
    const availability = useThisMacAvailability();
    const shown = availability === "shown" && form !== null;
    /* Shares the Settings pane's cache, so opening it after this is free. */
    const snapshot = useQuery({ queryKey: ["this-mac"], queryFn: () => thisMac.snapshot(), enabled: shown, staleTime: 30_000, retry: 0 });
    if (!shown || !snapshot.data || formConfigured(snapshot.data, form)) return null;
    return (
        <button
            type="button"
            className={compact ? "linkish thismac-setup" : "btn thismac-setup"}
            onClick={() => openSettings("this-mac-setup", form)}
        >
            <KeyIcon size={13} /> Set up on {noun}
        </button>
    );
}

/** "Set up an AI model on this Mac", beside a failure that no model explains.
 *
 *  Same rule as above: only where This Mac is shown, so a browser or a shared
 *  address never learns the machine has settings. Opens Server setup at the
 *  AI model card. */
export function SetUpAiModelLink({ compact = true, label }: { compact?: boolean; label?: string }) {
    const noun = useThisComputer();
    const availability = useThisMacAvailability();
    if (availability !== "shown") return null;
    return (
        <button
            type="button"
            className={compact ? "linkish thismac-setup" : "btn thismac-setup"}
            onClick={() => openSettings("this-mac-setup", "ai")}
        >
            <KeyIcon size={13} /> {label ?? `Set up an AI model on ${noun}`}
        </button>
    );
}
