"use client";

import type { ComponentType } from "react";
import type { WorkAnswer } from "./work";

/** The step after "What do you do?": plugins suggested for the answer.
 *
 *  Left empty on purpose. The plugin shelf and its install are built apart
 *  from the way in; when they land, the component goes here and the step
 *  shows up on every door, already named along the top ("Plugins") and
 *  handed what it needs. Until then `null` skips it and the person lands in
 *  their Lemma straight after answering. */
export interface PluginsStepProps {
    orgId: string;
    /** The Lemma they are about to land in: the one they were invited to,
     *  else the one everyone in the organization shares. */
    podId: string | null;
    /** What they said they do; empty when they skipped. */
    work: WorkAnswer | null;
    /** Done here. `podId` is where to land instead, when the step made or
     *  chose another Lemma. */
    onDone: (podId?: string | null) => void;
}

export const PluginsStep: ComponentType<PluginsStepProps> | null = null;
