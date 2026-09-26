"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { CloseIcon, WarningIcon } from "@/ui/icons";
import { openSettings } from "./open-settings";
import { thisMac } from "./this-mac";
import { checklistDismissed, noModelAnywhere } from "./server-setup";
import { useThisMacAvailability } from "./this-mac-settings";

/** "No AI model is connected yet", for as long as that is true.
 *
 *  The first-run checklist asks once; this is what stays after it is put
 *  away, because nothing a teammate is asked to do works until a model is
 *  set up. Only where This Mac is shown, and only when neither this
 *  computer's server nor the organization has a model provider -- a model
 *  added on Models is one teammates can run on, and a notice about "no
 *  model" beside it would be wrong. Dismissing it hides it until the page is
 *  opened again. */
export function NoModelNotice({ orgId }: { orgId: string | null }) {
    const availability = useThisMacAvailability();
    const shown = availability === "shown";
    const snapshot = useQuery({ queryKey: ["this-mac"], queryFn: () => thisMac.snapshot(), enabled: shown, staleTime: 30_000, retry: 0 });
    const runtimes = useQuery({
        queryKey: ["runtimes", orgId],
        queryFn: () => source.listRuntimes(orgId as string),
        enabled: shown && Boolean(orgId),
        staleTime: 5 * 60_000,
    });
    const [hidden, setHidden] = useState(false);
    if (hidden || !shown || !checklistDismissed()) return null;
    if (!noModelAnywhere(snapshot.data, runtimes.isSuccess ? runtimes.data : undefined)) return null;
    return (
        <div className="desk-notice" role="status" aria-live="polite" data-kind="failed">
            <span className="desk-notice__mark" aria-hidden="true"><WarningIcon size={17} /></span>
            <span className="desk-notice__body">
                <b>No AI model is connected yet</b>
                <span>
                    Teammates can’t reply until one is.{" "}
                    <button type="button" className="linkish" onClick={() => openSettings("this-mac-setup", "ai")}>Set one up</button>
                </span>
            </span>
            <button className="desk-notice__close icon-button" aria-label="Dismiss" title="Dismiss" onClick={() => setHidden(true)}>
                <CloseIcon size={14} />
            </button>
        </div>
    );
}
