"use client";

import { cn } from "@/lib/utils";

/**
 * The face beside a message.
 *
 * Initials on a colour derived from the id, because nobody in a pod has
 * uploaded a picture and a row of identical grey circles distinguishes nothing.
 * Deriving the colour from the id rather than the name keeps it stable when
 * somebody is renamed, and keeps two people called Deepak apart.
 */

/**
 * Eight colours, defined in CSS rather than computed here.
 *
 * A hue could be derived and set inline, and that is what this did first --
 * but per-person colour is data, not runtime geometry, and inline styles are
 * where a design system quietly stops applying. The palette lives with the
 * rest of the tokens; this only chooses which of them.
 */
const SWATCHES = 8;

function swatchFor(seed: string): number {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) | 0;
  }
  return Math.abs(hash) % SWATCHES;
}

/** One letter, or two from a two-word name. Never more — it stops being a face. */
function initialsFor(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 1).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

export function AssistantAvatar({
  name,
  seed,
  imageUrl,
  className,
}: {
  name: string;
  /** Stable identity — a user or agent id. Falls back to the name. */
  seed?: string | null;
  imageUrl?: string | null;
  className?: string;
}) {
  const swatch = swatchFor(seed || name);
  return (
    <span
      className={cn("lchat-avatar", `lchat-avatar-c${swatch}`, className)}
      aria-hidden="true"
    >
      {imageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={imageUrl} alt="" className="lchat-avatar-image" />
      ) : (
        initialsFor(name)
      )}
    </span>
  );
}
