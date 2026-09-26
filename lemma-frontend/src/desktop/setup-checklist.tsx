"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Modal } from "@/shell/modal";
import { openSettings } from "./open-settings";
import { useThisComputer } from "./this-computer";
import { thisMac } from "./this-mac";
import { CAPABILITIES, capabilityStatus, checklistDismissed, dismissChecklist, needsSetup, showChecklist } from "./server-setup";
import { useThisMacAvailability } from "./this-mac-settings";

/** The first thing a new local install shows: what its server can do, and
 *  the one thing it cannot do without — a model.
 *
 *  Once per install, in the app's own window. Everything but the AI model can
 *  be skipped, and all of it is on This Mac → Server setup afterwards, where
 *  the entry carries a dot until the model is set up. Choosing a row opens
 *  that card and puts this away for the session; "Skip for now" and "Done"
 *  put it away for good. */
export function SetupChecklist() {
    const noun = useThisComputer();
    const availability = useThisMacAvailability();
    const shown = availability === "shown";
    const snapshot = useQuery({ queryKey: ["this-mac"], queryFn: () => thisMac.snapshot(), enabled: shown, staleTime: 30_000, retry: 0 });
    const [dismissed, setDismissed] = useState(() => checklistDismissed());
    const [closedForNow, setClosedForNow] = useState(false);
    if (closedForNow || !showChecklist({ shown, snapshot: snapshot.data, dismissed })) return null;
    const data = snapshot.data!;
    const missing = needsSetup(data);
    const finish = () => { dismissChecklist(); setDismissed(true); };
    return (
        <Modal title={`Set up the server on ${noun}`} subtitle="Lemma runs its whole server here. It needs an AI model; the rest is optional and can wait." onClose={() => setClosedForNow(true)}>
            <div className="setup-checklist">
                {CAPABILITIES.map((capability) => {
                    const status = capabilityStatus(data, capability.id);
                    const tone = status.state === "ready" ? "ok" : status.state === "needs-setup" ? "warn" : "muted";
                    return (
                        <div className="setup-checklist__row" key={capability.id}>
                            <span className="thismac-row__text">
                                <span className="thismac-row__name">{capability.title}{capability.required ? " · required" : ""}</span>
                                <span className="thismac-row__said">{capability.unlocks}</span>
                            </span>
                            <span className={"mrow__state mrow__state--" + tone}><i aria-hidden="true" />{status.label}</span>
                            <button
                                type="button"
                                className={capability.required && status.state !== "ready" ? "btn btn--primary" : "btn"}
                                onClick={() => { setClosedForNow(true); openSettings("this-mac-setup", capability.id); }}
                            >
                                {status.state === "ready" ? "Review" : "Set up"}
                            </button>
                        </div>
                    );
                })}
                <div className="setup-checklist__acts">
                    <button type="button" className="btn" onClick={finish}>{missing.length ? "Skip for now" : "Done"}</button>
                </div>
                <p className="thismac-said">Find all of this later in Settings → {noun} → Server setup.</p>
            </div>
        </Modal>
    );
}
