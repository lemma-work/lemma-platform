"use client"

export type LemmaRecordsAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaRecordsSurface = "inherit" | "muted" | "card"
export type LemmaRecordsDensity = "compact" | "comfortable" | "spacious"
export type LemmaRecordsRadius = "none" | "sm" | "md" | "lg" | "xl"

export function recordsAppearanceFromSurface(
  surface: LemmaRecordsSurface,
): LemmaRecordsAppearance {
  if (surface === "inherit") return "borderless"
  if (surface === "muted") return "minimal"
  return "contained"
}

export function recordsSurfaceFromAppearance(
  appearance: LemmaRecordsAppearance,
): LemmaRecordsSurface {
  if (appearance === "borderless") return "inherit"
  if (appearance === "minimal") return "muted"
  return "card"
}

export function recordsRadiusClassName(
  radius: LemmaRecordsRadius = "lg",
  target: "surface" | "control" | "pill" | "overlay" = "surface",
): string {
  if (radius === "none") return "rounded-none"

  if (radius === "sm") {
    if (target === "control" || target === "pill") return "rounded-sm"
    return "rounded-md"
  }

  if (radius === "md") {
    if (target === "surface" || target === "overlay") return "rounded-lg"
    return "rounded-md"
  }

  if (radius === "xl") {
    if (target === "pill") return "rounded-full"
    if (target === "control") return "rounded-xl"
    return "rounded-2xl"
  }

  if (target === "pill") return "rounded-full"
  if (target === "control") return "rounded-lg"
  return "rounded-xl"
}
