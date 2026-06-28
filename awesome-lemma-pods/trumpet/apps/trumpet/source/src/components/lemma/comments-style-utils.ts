export type LemmaCommentsAppearance = "default" | "borderless" | "minimal" | "contained"
export type LemmaCommentsDensity = "compact" | "comfortable" | "spacious"
export type LemmaCommentsRadius = "none" | "sm" | "md" | "lg" | "xl" | "2xl" | "3xl" | "full"

const RADIUS_MAP: Record<LemmaCommentsRadius, Record<string, string>> = {
  none: { surface: "rounded-none", control: "rounded-none", pill: "rounded-none", overlay: "rounded-none" },
  sm:   { surface: "rounded-sm", control: "rounded-sm", pill: "rounded-full", overlay: "rounded-sm" },
  md:   { surface: "rounded-md", control: "rounded-md", pill: "rounded-full", overlay: "rounded-md" },
  lg:   { surface: "rounded-lg", control: "rounded-md", pill: "rounded-full", overlay: "rounded-lg" },
  xl:   { surface: "rounded-xl", control: "rounded-lg", pill: "rounded-full", overlay: "rounded-xl" },
  "2xl": { surface: "rounded-2xl", control: "rounded-xl", pill: "rounded-full", overlay: "rounded-2xl" },
  "3xl": { surface: "rounded-3xl", control: "rounded-2xl", pill: "rounded-full", overlay: "rounded-3xl" },
  full: { surface: "rounded-3xl", control: "rounded-full", pill: "rounded-full", overlay: "rounded-3xl" },
}

export function commentsRadiusClassName(
  radius: LemmaCommentsRadius,
  kind: "surface" | "control" | "pill" | "overlay",
): string {
  return RADIUS_MAP[radius]?.[kind] ?? RADIUS_MAP.lg[kind]
}
