export const NEW_CAST = [
    "knot", "arch", "kite", "coil", "bloom", "stack", "spark",
    "wedge", "cloud", "bolt", "ring", "sprout", "cube", "wave",
    "moon", "prism", "bean", "spool", "grid", "peak", "gem",
] as const;
export type NewCharacter = typeof NEW_CAST[number];
export const ALL_CAST = ["loop", "pleat", "frame", ...NEW_CAST] as const;
export type CastMember = typeof ALL_CAST[number];

/** The flourish a character adds to its greeting, on top of the shared wave.
 *  Data rather than a chain of `character === "…"` in the rig: the cast went
 *  from seven to twenty-four, and seventeen more string comparisons is not a
 *  design. Separate from that: `foot` overrides the boot tint where the torso
 *  colour would be wrong on a leg, `stance` widens the legs for a body with a
 *  gap under it, and `arm` raises a shoulder that would otherwise sit inside
 *  the torso. Those are geometry, not personality. */
export type Flourish = "twist" | "nod" | "step" | "spring" | "perk" | "shuffle" | "hop";

export const EXTENDED = {
    knot: { name: "Knot", heading: "Better, together.", intro: "A little twist. A warm welcome.", color: "#35b6b7", eyes: [[572, 467], [690, 447]], y: 65, scale: .8, duration: 3.8, waves: 2, tilt: -3, lift: 10, flourish: "twist", greeting: "An open-armed welcome and a little twist." },
    arch: { name: "Arch", heading: "Count on me.", intro: "Steady feet. A sunny disposition.", color: "#ffcf28", stance: "wide", eyes: [[568, 300], [690, 300]], y: 40, scale: .8, duration: 4.2, waves: 2, tilt: 1.5, lift: 3, flourish: "nod", greeting: "A slow hello, a reassuring nod, then stillness." },
    kite: { name: "Kite", heading: "What’s out there?", intro: "Curiosity with a spring in its step.", color: "#fc707e", foot: "#426cf5", eyes: [[677, 430], [790, 455]], y: 45, scale: .8, duration: 3.6, waves: 3, tilt: -5, lift: 18, flourish: "step", greeting: "A curious lean, a lifted foot, and a bright wave." },
    coil: { name: "Coil", heading: "An idea springs up.", intro: "A thoughtful pause. Then a little bounce.", color: "#7b83fa", eyes: [[671, 308], [787, 282]], y: 5, scale: .8, duration: 3.8, waves: 3, tilt: 2, lift: 27, flourish: "spring", greeting: "Squash, spring, smile. A soft rebound into a wave." },
    bloom: { name: "Bloom", heading: "You’re here!", intro: "A little encouragement goes a long way.", color: "#ff707d", foot: "#a47ae8", eyes: [[567, 521], [680, 521]], y: 15, scale: .8, duration: 4, waves: 2, tilt: -2, lift: 10, flourish: "perk", greeting: "An open-armed greeting with a happy little perk-up." },
    stack: { name: "Stack", heading: "One thing at a time.", intro: "Collected, considered, quietly cheerful.", color: "#38a98b", eyes: [[576, 381], [700, 375]], y: 60, scale: .8, duration: 4.3, waves: 2, tilt: 1, lift: 4, flourish: "shuffle", greeting: "A measured wave and a small settling shuffle." },
    spark: { name: "Spark", heading: "Let’s get started.", intro: "A bright hello. A little burst of joy.", color: "#ffd22d", arm: "high", eyes: [[619, 490], [733, 472]], y: 15, scale: .76, duration: 3.2, waves: 3, tilt: 4, lift: 38, flourish: "hop", greeting: "A tiny hop, a quick wave, then a gentle landing." },

    wedge: { name: "Wedge", heading: "A slice of the work.", intro: "Takes a piece and gets on with it.", color: "#1f9e9e", eyes: [[613, 463], [708, 463]], y: 73, scale: .83, duration: 4.2, waves: 2, tilt: 1.5, lift: 5, flourish: "shuffle", greeting: "A short wave, then a shift of weight and back to it." },
    cloud: { name: "Cloud", heading: "Thinking it over.", intro: "Slow to speak. Worth the wait.", color: "#7aa6f0", eyes: [[570, 451], [689, 451]], y: 64, scale: .85, duration: 4.4, waves: 2, tilt: -1.5, lift: 7, flourish: "perk", greeting: "A drifting hello that takes its own sweet time." },
    bolt: { name: "Bolt", heading: "Right away.", intro: "Quick off the mark, every time.", color: "#f58220", eyes: [[619, 253], [751, 280]], y: -71, scale: 1.08, duration: 3, waves: 3, tilt: 4, lift: 30, flourish: "hop", greeting: "There before you finish asking. A snap of a wave." },
    ring: { name: "Ring", heading: "Round we go.", intro: "Comes back to you. Always does.", color: "#d92d63", eyes: [[573, 244], [705, 244]], y: -85, scale: .94, duration: 3.8, waves: 2, tilt: -3, lift: 12, flourish: "twist", greeting: "A wave, a slow turn, and back where it started." },
    sprout: { name: "Sprout", heading: "Give it time.", intro: "Small now. Not for long.", color: "#4caf3f", eyes: [[584, 588], [687, 588]], y: 63, scale: .82, duration: 3.9, waves: 2, tilt: 2, lift: 14, flourish: "perk", greeting: "A shy wave and a little stretch towards the light." },
    cube: { name: "Cube", heading: "Square with you.", intro: "Plain-spoken and hard to topple.", color: "#8b3fd4", eyes: [[573, 560], [685, 560]], y: 64, scale: .8, duration: 4.3, waves: 2, tilt: 1, lift: 4, flourish: "nod", greeting: "One wave, one nod. Nothing decorative about it." },
    wave: { name: "Wave", heading: "Go with it.", intro: "Rolls with whatever the day brings.", color: "#2f5fe0", eyes: [[701, 353], [834, 353]], y: 27, scale: 1.02, duration: 3.7, waves: 3, tilt: -3, lift: 15, flourish: "twist", greeting: "A hello that rolls through from the feet up." },
    moon: { name: "Moon", heading: "Still up.", intro: "Keeps the late watch without fuss.", color: "#f5c43a", eyes: [[399, 519], [500, 519]], y: 65, scale: .81, duration: 4.4, waves: 2, tilt: -1.5, lift: 6, flourish: "nod", greeting: "A quiet wave from somebody who never went home." },
    prism: { name: "Prism", heading: "Another angle.", intro: "Turns the thing until it makes sense.", color: "#2bb3a6", eyes: [[589, 625], [693, 625]], y: 65, scale: .8, duration: 3.8, waves: 2, tilt: 3, lift: 11, flourish: "twist", greeting: "A wave, then a tilt to see you from one side." },
    bean: { name: "Bean", heading: "Comfy here.", intro: "Unhurried, and glad you stopped by.", color: "#a880e8", eyes: [[494, 336], [625, 336]], y: -73, scale: 1.05, duration: 4.3, waves: 2, tilt: -2, lift: 6, flourish: "spring", greeting: "A soft wave and a settle, like sitting back down." },
    spool: { name: "Spool", heading: "Winding up.", intro: "Keeps the thread from getting lost.", color: "#e8661f", eyes: [[560, 420], [690, 420]], y: -60, scale: 1, duration: 3.9, waves: 3, tilt: 2, lift: 13, flourish: "spring", greeting: "A wave that winds up and then unwinds again." },
    grid: { name: "Grid", heading: "All of it, in order.", intro: "Everything has a square to sit in.", color: "#2f7a4a", eyes: [[551, 374], [664, 374]], y: 75, scale: .83, duration: 4.3, waves: 2, tilt: 1, lift: 4, flourish: "shuffle", greeting: "A tidy wave and a small shuffle back into line." },
    peak: { name: "Peak", heading: "Nearly at the top.", intro: "Likes the hard half of the climb.", color: "#4a7fd4", eyes: [[552, 610], [709, 610]], y: -208, scale: 1.12, duration: 3.4, waves: 3, tilt: -4, lift: 22, flourish: "step", greeting: "A wave from higher up, and one foot already moving." },
    gem: { name: "Gem", heading: "Worth a second look.", intro: "Finds the bit everyone else walked past.", color: "#e8318f", eyes: [[619, 550], [814, 550]], y: -163, scale: 1.15, duration: 3.5, waves: 3, tilt: 3, lift: 20, flourish: "spring", greeting: "A bright wave with a glint of having spotted something." },
} as const;
