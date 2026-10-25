import { describe, expect, it } from "vitest";
import { RESERVED, identityHue } from "../src/ui/hue";

const ids = Array.from({ length: 2000 }, (_, index) => `lemma-${index.toString(36)}-${(index * 7919).toString(16)}`);

describe("a Lemma's colour", () => {
    it("never falls in violet's band or the warm band that reads as amber", () => {
        for (const id of ids) {
            const hue = identityHue(id);
            for (const [from, to] of RESERVED) expect(hue < from || hue > to, `${id} → ${hue}`).toBe(true);
        }
    });

    it("keeps moved Lemmas apart instead of piling them on one hue", () => {
        const moved = new Set(ids.map(identityHue).filter((hue) => (hue >= 320 && hue < 360) || (hue > 80 && hue < 120)).map(Math.round));
        expect(moved.size).toBeGreaterThan(30);
    });

    it("is the same for the same Lemma every time", () => {
        expect(identityHue("abc")).toBe(identityHue("abc"));
    });
});
