"use client";

import type { Pod } from "@/data";
import { Mark, markTint } from "@/shell/mark";

/** A teammate's face: the whole character, standing on its own tint.
 *
 *  Rounded, never round. A circle is a person — initials on a disc — and a
 *  list holding both has to tell them apart at a glance. The tint comes from
 *  the same hash as the mark's, so a teammate is one colour on the rail, at
 *  the top of its sidebar, in the breadcrumb and on its card.
 *
 *  Still unless asked. A rig per face is what once made iPhones stop painting
 *  the names beside a column of them, so only the face a page is about — its
 *  About, its Home — is `live`. */
export function TeammateFace({ pod, size, live = false, className }: {
    pod: Pick<Pod, "id" | "name" | "iconUrl">;
    size: number;
    live?: boolean;
    className?: string;
}) {
    return (
        <span
            className={"tface" + (className ? " " + className : "")}
            style={{ width: size, height: size, background: markTint(pod.id).background }}
            aria-hidden="true"
        >
            <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={Math.round(size * 0.86)} still={!live} />
        </span>
    );
}
